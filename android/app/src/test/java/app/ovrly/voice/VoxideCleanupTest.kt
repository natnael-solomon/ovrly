package app.ovrly.voice

import java.net.InetAddress
import java.net.Socket
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import javax.net.SocketFactory
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotSame
import org.junit.Assert.assertTrue
import org.junit.Test

class VoxideCleanupTest {
    @Test
    fun closingTransportEvictsRealPooledSocketOffCallerThread() {
        val caller = Thread.currentThread()
        val closed = CountDownLatch(1)
        val closeThread = AtomicReference<Thread>()
        val client = OkHttpClient.Builder()
            .socketFactory(TrackingSockets(closeThread, closed))
            .build()
        val server = MockWebServer()
        server.start(InetAddress.getByName("127.0.0.1"), 0)
        val transport = VoxideTransport(
            VoiceConfiguration(true, "https://example.invalid", "vox_pub_fixture"),
            client,
            VoiceDiagnostics {}
        )
        try {
            server.enqueue(MockResponse().setBody("synthetic"))
            val url = server.url("/").newBuilder().host("127.0.0.1").build()
            client.newCall(Request.Builder().url(url).build()).execute().use {
                assertEquals("synthetic", it.body.string())
            }
            assertEquals(1, client.connectionPool.idleConnectionCount())
            transport.close()
            transport.close()
            assertFalse(transport.send("{}"))
            assertTrue("Pooled socket must be released", closed.await(5, TimeUnit.SECONDS))
            assertNotSame(
                "Socket close can write TLS bytes; never run it on the UI caller",
                caller,
                closeThread.get()
            )
            assertTrue(client.dispatcher.executorService.awaitTermination(5, TimeUnit.SECONDS))
            assertEquals(0, client.connectionPool.connectionCount())
        } finally {
            transport.close()
            server.shutdown()
        }
    }

    private class TrackingSockets(
        private val closeThread: AtomicReference<Thread>,
        private val closed: CountDownLatch
    ) : SocketFactory() {
        override fun createSocket(): Socket = object : Socket() {
            override fun close() {
                closeThread.compareAndSet(null, Thread.currentThread())
                try {
                    super.close()
                } finally {
                    closed.countDown()
                }
            }
        }

        override fun createSocket(host: String, port: Int): Socket = error("Unused socket overload")

        override fun createSocket(
            host: String,
            port: Int,
            local: InetAddress,
            localPort: Int
        ): Socket = error("Unused socket overload")

        override fun createSocket(host: InetAddress, port: Int): Socket =
            error("Unused socket overload")

        override fun createSocket(
            host: InetAddress,
            port: Int,
            local: InetAddress,
            localPort: Int
        ): Socket = error("Unused socket overload")
    }
}
