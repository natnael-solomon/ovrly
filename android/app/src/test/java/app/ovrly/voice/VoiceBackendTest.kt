package app.ovrly.voice

import app.ovrly.contract.ContractFixtures
import app.ovrly.contract.Investigation
import app.ovrly.contract.InvestigationCodec
import app.ovrly.contract.VoiceActionCodec
import app.ovrly.data.ApiResult
import app.ovrly.data.ApiTestServer
import app.ovrly.data.VoiceApi
import app.ovrly.data.withServer
import java.util.concurrent.atomic.AtomicInteger
import kotlin.coroutines.CoroutineContext
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.filterNotNull
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceBackendTest {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val opened = mutableListOf<String>()
    private val refreshed = AtomicInteger()
    private val ids = AtomicInteger()

    @After
    fun cleanUp() = scope.cancel()

    private val effects = object : VoiceEffects {
        override fun openCheck(investigationId: String) {
            opened += investigationId
        }

        override suspend fun refreshChecks() {
            refreshed.incrementAndGet()
        }
    }

    private fun ApiTestServer.backend(confirmations: VoiceConfirmations = VoiceConfirmations()) =
        VoiceBackend(
            VoiceApi(api),
            VoiceTargets(jobs),
            confirmations,
            effects,
            scope,
            requestIds = { "voice-req-${ids.incrementAndGet()}" }
        )

    private fun investigation(name: String): Investigation = InvestigationCodec.parseInvestigation(
        ApiTestServer.result(name).investigationPayload()
    )

    private fun ApiTestServer.seed(vararg names: String) = runBlocking {
        for (name in names) {
            jobs.recordRead(investigation(name))
            wall.addAndGet(1_000)
        }
    }

    private fun ApiTestServer.respond(action: String, kind: String, id: String, accepted: Boolean) {
        val request = "voice-req-${ids.get() + 1}"
        val body = JSONObject()
            .put("request_id", request)
            .put("result", if (accepted) "accepted" else "denied")
            .put("action", action)
            .put("target", JSONObject().put("kind", kind).put("id", id))
            .put("message", if (accepted) "Done." else "No such target is visible.")
        if (!accepted) {
            body.put(
                "error",
                JSONObject().put("code", "VOICE_TARGET_NOT_FOUND")
                    .put("message", "No such target is visible to the caller.")
                    .put("retryable", false).put("action", "fix_request")
                    .put("request_id", request)
            )
        }
        json(200, body.toString())
    }

    private fun ApiTestServer.sentTarget(): Pair<String, String> {
        val body = JSONObject(take().body.readUtf8())
        return body.getString("action") to body.getJSONObject("target").getString("id")
    }

    @Test
    fun latestResolvesToTheNewestCheckForEachTargetKind() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        server.seed("failed", "complete")
        val complete = investigation("complete")
        val backend = server.backend()
        server.respond("open_check", "investigation", complete.id, accepted = true)
        val open = VoiceCommand(VoiceCommandAction.OPEN_CHECK, "latest")
        assertTrue(runBlocking { backend.run(open) }.success)
        assertEquals("open_check" to complete.id, server.sentTarget())
        assertEquals(listOf(complete.id), opened)
        server.respond("save_report", "report", complete.report!!.id, accepted = true)
        runBlocking { backend.run(VoiceCommand(VoiceCommandAction.SAVE_REPORT, "latest")) }
        assertEquals("save_report" to complete.report!!.id, server.sentTarget())
        server.respond("queue_retry", "job", complete.job!!.id, accepted = true)
        runBlocking { backend.run(VoiceCommand(VoiceCommandAction.QUEUE_RETRY, "latest")) }
        assertEquals("queue_retry" to complete.job!!.id, server.sentTarget())
        assertEquals("only queue actions refresh the store", 1, refreshed.get())
    }

    @Test
    fun anExplicitIdIsSentAsGivenAndADenialHasNoEffect() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        server.respond("open_check", "investigation", "Inv_Other-1", accepted = false)
        val outcome = runBlocking {
            server.backend().run(VoiceCommand(VoiceCommandAction.OPEN_CHECK, "Inv_Other-1"))
        }
        assertFalse(outcome.success)
        assertEquals("VOICE_TARGET_NOT_FOUND", outcome.code)
        assertEquals("No such target is visible.", outcome.message)
        assertEquals("open_check" to "Inv_Other-1", server.sentTarget())
        assertTrue(opened.isEmpty())
    }

    @Test
    fun latestWithoutAUsableTargetNeverCallsTheServer() = withServer { server ->
        val backend = server.backend()
        val open = VoiceCommand(VoiceCommandAction.OPEN_CHECK, "latest")
        val none = runBlocking { backend.run(open) }
        assertFalse(none.success)
        assertEquals(VoiceTargets.NO_CHECKS, none.message)
        server.seed("failed")
        val noReport = runBlocking {
            backend.run(VoiceCommand(VoiceCommandAction.SAVE_REPORT, "latest"))
        }
        assertEquals(VoiceBackend.NO_TARGET, noReport.code)
        assertEquals(0, server.server.requestCount)
    }

    @Test
    fun theAssistantSeesIdsAndStatusButNoSourceUrl() = withServer { server ->
        server.seed("complete")
        val checks = runBlocking { VoiceTargets(server.jobs).recent() }
        val state = VoiceTargets.state(checks)
        val check = state.getJSONArray("checks").getJSONObject(0)
        val complete = investigation("complete")
        assertEquals(complete.id, check.getString("investigation_id"))
        assertEquals(complete.report!!.id, check.getString("report_id"))
        assertEquals(complete.job!!.id, check.getString("job_id"))
        assertEquals("complete", check.getString("status"))
        assertFalse("http" in state.toString())
    }

    @Test
    fun cancellingNeedsThisExactRequestApprovedOnScreen() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        val confirmations = VoiceConfirmations(timeoutMillis = 5_000)
        val backend = server.backend(confirmations)
        val cancel = VoiceCommand(VoiceCommandAction.QUEUE_CANCEL, "job_synthetic_1")
        // Declined: nothing is sent.
        val declined = runBlocking {
            val run = scope.async { backend.run(cancel) }
            val pending = withTimeout(5_000) { confirmations.pending.filterNotNull().first() }
            assertEquals(cancel.target, pending.target)
            confirmations.answer(pending.id + 1, true)
            confirmations.answer(pending.id, false)
            run.await()
        }
        assertEquals(VoiceBackend.CONFIRMATION_DECLINED, declined.code)
        assertEquals(0, server.server.requestCount)
        // Approved: sent once, then the store is refreshed.
        server.respond("queue_cancel", "job", cancel.target, accepted = true)
        val approved = runBlocking {
            val run = scope.async { backend.run(cancel) }
            val pending = withTimeout(5_000) { confirmations.pending.filterNotNull().first() }
            confirmations.answer(pending.id, true)
            run.await()
        }
        assertTrue(approved.success)
        assertEquals("queue_cancel" to cancel.target, server.sentTarget())
        assertEquals(1, refreshed.get())
        assertNull(confirmations.pending.value)
    }

    @Test
    fun stoppingVoiceOrTimingOutDiscardsThePendingApproval() = withServer { server ->
        val confirmations = VoiceConfirmations(timeoutMillis = 200)
        val backend = server.backend(confirmations)
        val cancel = VoiceCommand(VoiceCommandAction.QUEUE_CANCEL, "job_synthetic_1")
        val timedOut = runBlocking { backend.run(cancel) }
        assertEquals(VoiceBackend.CONFIRMATION_DECLINED, timedOut.code)
        val discarded = runBlocking {
            val run = scope.async { backend.run(cancel) }
            withTimeout(5_000) { confirmations.pending.filterNotNull().first() }
            confirmations.discardAll()
            run.await()
        }
        assertEquals(VoiceBackend.CONFIRMATION_DECLINED, discarded.code)
        assertEquals(0, server.server.requestCount)
        assertNull(confirmations.pending.value)
    }

    @Test
    fun cancellationsQueuedOrStillResolvingWhenVoiceStopsNeverAsk() = withServer { server ->
        server.credentials.write("synthetic-token-1")
        val confirmations = VoiceConfirmations(timeoutMillis = 5_000)
        val backend = server.backend(confirmations)
        val first = VoiceCommand(VoiceCommandAction.QUEUE_CANCEL, "job_synthetic_1")
        val second = VoiceCommand(VoiceCommandAction.QUEUE_CANCEL, "job_synthetic_2")
        val (outcomes, shown) = runBlocking {
            // Undispatched: both commands have started (and read the epoch) before the stop.
            val runs = listOf(first, second).map {
                scope.async(start = CoroutineStart.UNDISPATCHED) { backend.run(it) }
            }
            val shown = withTimeout(5_000) { confirmations.pending.filterNotNull().first() }
            confirmations.discardAll()
            runs.map { it.await() } to shown
        }
        assertTrue(outcomes.all { it.code == VoiceBackend.CONFIRMATION_DECLINED })
        assertNull(confirmations.pending.value)
        // A command that started before the stop and reaches the dialog afterwards.
        val started = confirmations.epoch
        confirmations.discardAll()
        assertFalse(runBlocking { confirmations.confirm(second.action, second.target, started) })
        assertNull(confirmations.pending.value)
        assertEquals(0, server.server.requestCount)
        // Only the first dialog was ever shown: the next one gets the next id.
        val next = runBlocking {
            val run = scope.async { confirmations.confirm(first.action, first.target) }
            val pending = withTimeout(5_000) { confirmations.pending.filterNotNull().first() }
            confirmations.answer(pending.id, false)
            run.await()
            pending.id
        }
        assertEquals(shown.id + 1, next)
    }

    @Test
    fun aStopBetweenDispatchAndTheCommandRunningStillDiscardsTheConfirmation() =
        withServer { server ->
            val queued = ArrayDeque<Runnable>()
            val held = object : CoroutineDispatcher() {
                override fun dispatch(context: CoroutineContext, block: Runnable) {
                    queued.addLast(block)
                }
            }
            val confirmations = VoiceConfirmations(timeoutMillis = 5_000)
            val order = mutableListOf<String>()
            val tracked = object : VoiceEffects by effects {
                override suspend fun finished() {
                    order += "finished"
                }
            }
            val backend = VoiceBackend(
                VoiceApi(server.api),
                VoiceTargets(server.jobs),
                confirmations,
                tracked,
                CoroutineScope(held)
            )
            var outcome: VoiceCommandOutcome? = null
            val cancel = VoiceCommand(VoiceCommandAction.QUEUE_CANCEL, "job_synthetic_1")
            backend.execute(cancel) {
                order += "done"
                outcome = it
            }
            // Voice stops on the main thread before the background side starts the command.
            confirmations.discardAll()
            while (queued.isNotEmpty()) queued.removeFirst().run()
            assertEquals(VoiceBackend.CONFIRMATION_DECLINED, outcome?.code)
            assertEquals(listOf("finished", "done"), order)
            assertNull(confirmations.pending.value)
            assertEquals(0, server.server.requestCount)
            // No dialog was ever shown: the next one is the first.
            val first = runBlocking {
                val run = scope.async { confirmations.confirm(cancel.action, cancel.target) }
                val pending = withTimeout(5_000) { confirmations.pending.filterNotNull().first() }
                confirmations.answer(pending.id, false)
                run.await()
                pending.id
            }
            assertEquals(1L, first)
        }

    @Test
    fun theSharedFixturesUseExactlyTheClientAllowlist() {
        val fixtures = ContractFixtures.load(ContractFixtures.SHARED)
        val actions = fixtures.filter { it.expectRequest == "valid" }
            .map { JSONObject(it.requestPayload()).getString("action") }
            .toSet()
        assertEquals(VoiceCommandAction.entries.map { it.wireName }.toSet(), actions)
        // The server's 200 denial for an action outside the allowlist is a visible failure.
        val unsupported = fixtures.single { it.name == "unsupported-action" }
        val response = VoiceActionCodec.parseResponse(unsupported.responsePayload())
        val outcome = VoiceBackend.outcome(ApiResult.Success(response, response.requestId))
        assertFalse(outcome.success)
        assertEquals("VOICE_ACTION_UNSUPPORTED", outcome.code)
    }
}
