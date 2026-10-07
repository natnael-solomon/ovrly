package app.ovrly.data

import android.content.Context
import android.util.Base64
import androidx.credentials.CustomCredential
import androidx.credentials.GetCredentialRequest
import androidx.test.ext.junit.runners.AndroidJUnit4
import app.ovrly.testing.Device
import com.google.android.libraries.identity.googleid.GetSignInWithGoogleOption
import com.google.android.libraries.identity.googleid.GoogleIdTokenCredential
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

/**
 * The Google ID token path of [GoogleIdTokenSource] on a device (AN-10, #36), with real
 * Credential Manager types and a fake fetcher in place of the account picker: the request
 * asks for "Sign in with Google" for the Web client ID, and the returned credential's ID token
 * is what the account link sends. Nothing contacts Google.
 */
@RunWith(AndroidJUnit4::class)
class GoogleSignInDeviceTest {
    private val clientId =
        "123456789012-abcdefghijklmnop0123456789abcdef.apps.googleusercontent.com"

    @Test
    fun theButtonFlowReturnsTheGoogleIdToken() = runBlocking {
        var asked: GetCredentialRequest? = null
        val google = GoogleIdTokenCredential.Builder()
            .setId("synthetic.user@example.com")
            .setIdToken(SYNTHETIC_TOKEN)
            .build()
        val fetcher = object : CredentialFetcher {
            override suspend fun fetch(activity: Context, request: GetCredentialRequest) =
                CustomCredential(google.type, google.data).also { asked = request }
        }
        val result = GoogleIdTokenSource(clientId, fetcher).idToken(Device.context)

        assertEquals(SYNTHETIC_TOKEN, (result as IdTokenResult.Token).value)
        val option = asked?.credentialOptions?.single()
        assertTrue(option is GetSignInWithGoogleOption)
        assertEquals(clientId, (option as GetSignInWithGoogleOption).serverClientId)
    }

    private companion object {
        /** An unsigned JWT-shaped token: the library reads its payload, nothing verifies it. */
        val SYNTHETIC_TOKEN = listOf(
            """{"alg":"none","typ":"JWT"}""",
            """{"sub":"synthetic-subject","email":"synthetic.user@example.com"}""",
            "synthetic-signature"
        ).joinToString(".") {
            Base64.encodeToString(
                it.toByteArray(),
                Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING
            )
        }
    }
}
