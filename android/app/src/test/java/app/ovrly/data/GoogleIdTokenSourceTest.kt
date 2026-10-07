package app.ovrly.data

import android.content.Context
import android.content.ContextWrapper
import android.os.Bundle
import androidx.credentials.CustomCredential
import androidx.credentials.GetCredentialRequest
import androidx.credentials.exceptions.GetCredentialCancellationException
import androidx.credentials.exceptions.GetCredentialException
import androidx.credentials.exceptions.GetCredentialInterruptedException
import androidx.credentials.exceptions.NoCredentialException
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** The Credential Manager source (#36) with a fake fetcher; nothing contacts Google. */
class GoogleIdTokenSourceTest {
    private val clientId =
        "123456789012-abcdefghijklmnop0123456789abcdef.apps.googleusercontent.com"
    private val activity: Context = ContextWrapper(null)

    private class Fetcher(private val answer: () -> CustomCredential) : CredentialFetcher {
        var calls = 0

        override suspend fun fetch(activity: Context, request: GetCredentialRequest) =
            answer().also { calls++ }
    }

    private fun failing(error: GetCredentialException) = Fetcher { throw error }

    @Test
    fun onlyAWebClientIdEnablesSignIn() {
        assertTrue(GoogleIdTokenSource.isWebClientId(clientId))
        listOf(
            "",
            "abc.apps.googleusercontent.com",
            "123-abc.apps.googleusercontent.com.evil",
            "123-ABC.apps.googleusercontent.com"
        ).forEach { assertFalse(it, GoogleIdTokenSource(it).available) }
        assertTrue(GoogleIdTokenSource(clientId).available)
    }

    @Test
    fun noClientIdOrNoScreenAsksNothing() = runBlocking {
        val fetcher = Fetcher { error("must not be called") }
        assertEquals(
            IdTokenResult.Unavailable(SIGN_IN_UNAVAILABLE),
            GoogleIdTokenSource("", fetcher).idToken(activity)
        )
        assertEquals(
            IdTokenResult.Unavailable(SIGN_IN_UNAVAILABLE),
            GoogleIdTokenSource(clientId, fetcher).idToken(null)
        )
        assertEquals(0, fetcher.calls)
    }

    @Test
    fun pickerOutcomesMapToPlainResults() = runBlocking {
        val cases = mapOf<GetCredentialException, IdTokenResult>(
            GetCredentialCancellationException("closed") to IdTokenResult.Cancelled,
            NoCredentialException("none") to
                IdTokenResult.Unavailable(GoogleIdTokenSource.NO_ACCOUNT),
            GetCredentialInterruptedException("busy") to
                IdTokenResult.Unavailable(GoogleIdTokenSource.SIGN_IN_FAILED)
        )
        cases.forEach { (error, expected) ->
            assertEquals(
                error.javaClass.simpleName,
                expected,
                GoogleIdTokenSource(clientId, failing(error)).idToken(activity)
            )
        }
    }

    @Test
    fun aCredentialThatIsNotAGoogleIdTokenIsRefused() = runBlocking {
        val other = Fetcher { CustomCredential("com.example.OTHER", Bundle()) }
        assertEquals(
            IdTokenResult.Unavailable(GoogleIdTokenSource.NOT_GOOGLE),
            GoogleIdTokenSource(clientId, other).idToken(activity)
        )
        assertEquals(1, other.calls)
    }
}
