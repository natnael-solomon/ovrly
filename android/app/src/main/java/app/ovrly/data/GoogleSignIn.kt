package app.ovrly.data

import android.content.Context
import androidx.credentials.CredentialManager
import androidx.credentials.CustomCredential
import androidx.credentials.GetCredentialRequest
import androidx.credentials.exceptions.GetCredentialCancellationException
import androidx.credentials.exceptions.GetCredentialException
import androidx.credentials.exceptions.GetCredentialUnknownException
import androidx.credentials.exceptions.NoCredentialException
import com.google.android.libraries.identity.googleid.GetSignInWithGoogleOption
import com.google.android.libraries.identity.googleid.GoogleIdTokenCredential
import com.google.android.libraries.identity.googleid.GoogleIdTokenParsingException

/*
 * Google sign-in through Android Credential Manager (AN-10, #36; BC-D07). The app asks for a
 * Google ID token issued for the backend's Web client ID and sends it once to
 * `POST /v1/principals/link`; no client secret is on the device. Google matches this app to
 * its Android OAuth clients by package name and signing certificate, so the Android client
 * IDs are not part of the build.
 */

/** What [GoogleIdTokenSource] needs from Credential Manager; tests replace it. */
internal interface CredentialFetcher {
    /** Returns the credential type and its data bundle, or throws [GetCredentialException]. */
    suspend fun fetch(activity: Context, request: GetCredentialRequest): CustomCredential
}

/** The real fetcher: the account picker shown over [activity]. */
internal object CredentialManagerFetcher : CredentialFetcher {
    override suspend fun fetch(activity: Context, request: GetCredentialRequest): CustomCredential {
        val credential = CredentialManager.create(activity).getCredential(activity, request)
            .credential
        return credential as? CustomCredential
            ?: throw GetCredentialUnknownException("Not a Google credential")
    }
}

/**
 * Google ID tokens for [webClientId], the backend's `OVRLY_GOOGLE_CLIENT_ID`. Unavailable when
 * the build has no client ID. Only the "Sign in with Google" button flow is used, so a token
 * is requested only after the user taps it.
 */
internal class GoogleIdTokenSource(
    private val webClientId: String,
    private val fetcher: CredentialFetcher = CredentialManagerFetcher
) : IdTokenSource {
    override val available: Boolean get() = isWebClientId(webClientId)

    override suspend fun idToken(activity: Context?): IdTokenResult {
        if (!available || activity == null) return IdTokenResult.Unavailable(SIGN_IN_UNAVAILABLE)
        val request = GetCredentialRequest.Builder()
            .addCredentialOption(GetSignInWithGoogleOption.Builder(webClientId).build())
            .build()
        return try {
            val credential = fetcher.fetch(activity, request)
            if (credential.type != GoogleIdTokenCredential.TYPE_GOOGLE_ID_TOKEN_CREDENTIAL) {
                IdTokenResult.Unavailable(NOT_GOOGLE)
            } else {
                IdTokenResult.Token(GoogleIdTokenCredential.createFrom(credential.data).idToken)
            }
        } catch (_: GetCredentialCancellationException) {
            IdTokenResult.Cancelled
        } catch (_: NoCredentialException) {
            IdTokenResult.Unavailable(NO_ACCOUNT)
        } catch (_: GetCredentialException) {
            IdTokenResult.Unavailable(SIGN_IN_FAILED)
        } catch (_: GoogleIdTokenParsingException) {
            IdTokenResult.Unavailable(NOT_GOOGLE)
        }
    }

    companion object {
        private val CLIENT_ID = Regex("[0-9]+-[0-9a-z]+\\.apps\\.googleusercontent\\.com")

        const val NO_ACCOUNT =
            "No Google account is available on this device. Add one in Android settings."
        const val SIGN_IN_FAILED = "Google sign-in did not complete. Try again."
        const val NOT_GOOGLE = "Google sign-in returned something this app cannot use."

        /** A Google OAuth Web client ID, as the build accepts it; empty is not one. */
        fun isWebClientId(value: String): Boolean = CLIENT_ID.matches(value)
    }
}
