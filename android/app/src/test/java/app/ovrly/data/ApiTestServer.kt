package app.ovrly.data

import app.ovrly.contract.ContractFixtures
import java.net.InetAddress
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest

/** A loopback MockWebServer with an [ApiClient] in front of it and a controllable clock. */
internal class ApiTestServer(
    coldStart: ColdStartPolicy = ColdStartPolicy(wakingHintMillis = 60_000)
) : AutoCloseable {
    val server = MockWebServer().apply { start(InetAddress.getByName("127.0.0.1"), 0) }
    val log = CopyOnWriteArrayList<String>()
    val clock = AtomicLong(1_000_000)
    val client = ApiClient(
        server.url("/").newBuilder().host("127.0.0.1").build(),
        ApiClient.defaultHttpClient(),
        { log += it },
        coldStart,
        { clock.get() }
    )
    val credentials = MemoryCredentialStore()
    val api = OvrlyApi(client, credentials)

    /** Wall clock for the store, separate from the client's monotonic [clock]. */
    val wall = AtomicLong(1_700_000_000_000)
    val jobs = LocalJobs(MemoryInvestigationDao(), { wall.get() })

    fun repository(polling: PollingPolicy = PollingPolicy()) =
        InvestigationRepository(api, jobs, polling)

    fun services(polling: PollingPolicy = PollingPolicy(1, 1, 1)) =
        ApiServices(api, jobs, repository(polling))

    fun enqueue(response: MockResponse) = server.enqueue(response)

    fun json(status: Int, body: String) = enqueue(
        MockResponse().setResponseCode(status).setHeader("Content-Type", "application/json")
            .setBody(body)
    )

    fun guest(token: String = "synthetic-token-1") = json(
        201,
        """{"principal_id":"00000000-0000-4000-8000-000000000001","kind":"guest",""" +
            """"credential":{"token":"$token","token_type":"bearer"}}"""
    )

    fun error(
        status: Int,
        code: String,
        retryable: Boolean = false,
        action: String = "none",
        requestId: String = "synthetic-request"
    ) = json(
        status,
        """{"code":"$code","message":"Synthetic $code","retryable":$retryable,""" +
            """"action":"$action","request_id":"$requestId"}"""
    )

    fun take(): RecordedRequest = checkNotNull(server.takeRequest(5, TimeUnit.SECONDS)) {
        "No request reached the server"
    }

    override fun close() = server.shutdown()

    companion object {
        fun intakeResponse(name: String): String = ContractFixtures.load(ContractFixtures.INTAKE)
            .single { it.name == name }
            .responsePayload()

        fun result(name: String): ContractFixtures.Fixture =
            ContractFixtures.load(ContractFixtures.RESULTS).single { it.name == name }
    }
}

/** Runs [block] against a fresh [ApiTestServer]; returns Unit so JUnit methods stay void. */
internal fun withServer(
    coldStart: ColdStartPolicy = ColdStartPolicy(wakingHintMillis = 60_000),
    block: (ApiTestServer) -> Unit
) {
    ApiTestServer(coldStart).use(block)
}
