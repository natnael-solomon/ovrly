package app.ovrly.data

import android.content.Context
import androidx.room.Dao
import androidx.room.Query
import androidx.room.Transaction
import app.ovrly.contract.ContractJson
import app.ovrly.contract.ContractSyntax
import java.io.File
import java.io.IOException
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/*
 * Optional account link (AN-10, #36; BC-D07). Checking never needs it: every call works with
 * the guest credential. Linking upgrades this device's guest to a Google account in place, or,
 * when the account already exists on another device, swaps this device to the account's
 * credential. The Google ID token comes from an [IdTokenSource]: [GoogleIdTokenSource] when the
 * build has a Google Web client ID, otherwise [NoIdTokenSource], which reports sign-in as
 * unavailable.
 */

/** Where a Google ID token comes from. Tests inject a fake; no test contacts Google. */
internal interface IdTokenSource {
    /** False when this build cannot sign in, for example with no OAuth client configured. */
    val available: Boolean

    /**
     * Asks for an ID token. [activity] is the screen the account picker is shown over; a
     * source that needs one answers [IdTokenResult.Unavailable] without it.
     */
    suspend fun idToken(activity: Context?): IdTokenResult
}

internal sealed interface IdTokenResult {
    /** An ID token to send once to `POST /v1/principals/link`; it is never stored or logged. */
    class Token(val value: String) : IdTokenResult {
        override fun toString() = "Token(<redacted>)"
    }

    /** The user closed the account picker; nothing changes. */
    data object Cancelled : IdTokenResult

    /** Sign-in cannot run on this build or device. */
    data class Unavailable(val reason: String) : IdTokenResult
}

/** The build default: no Google OAuth client is configured, so there is no ID token. */
internal object NoIdTokenSource : IdTokenSource {
    override val available: Boolean = false

    override suspend fun idToken(activity: Context?): IdTokenResult =
        IdTokenResult.Unavailable(SIGN_IN_UNAVAILABLE)
}

internal const val SIGN_IN_UNAVAILABLE = "Sign-in isn't available in this build."

/** Body of `POST /v1/principals/link`; only Google is accepted (BC-D07). */
@Serializable
internal data class AccountLinkRequest(
    val provider: String,
    @SerialName("id_token")
    val idToken: String
) {
    init {
        require(provider == GOOGLE) { "provider must be google" }
        ContractSyntax.text("id_token", idToken, max = MAX_ID_TOKEN_LENGTH)
    }

    override fun toString() = "AccountLinkRequest(provider=$provider, idToken=<redacted>)"

    companion object {
        const val GOOGLE = "google"
        const val MAX_ID_TOKEN_LENGTH = 4096
    }
}

/**
 * `200` of `POST /v1/principals/link`. [credential] is null when this principal was upgraded
 * in place, and the account's new credential when this device continues as an account that
 * already existed (the guest credential is revoked by then).
 */
@Serializable
internal data class AccountLinkResponse(
    @SerialName("principal_id")
    val principalId: String,
    val kind: String,
    val linked: Boolean,
    @SerialName("merged_saved_reports")
    val mergedSavedReports: Int,
    val credential: GuestCredential?
) {
    init {
        ContractSyntax.uuid("principal_id", principalId)
        require(kind == "account") { "kind must be account" }
        require(linked) { "linked must be true" }
        require(mergedSavedReports >= 0) { "merged_saved_reports must not be negative" }
    }
}

internal object AccountLinkCodec {
    fun encodeRequest(request: AccountLinkRequest): String = ContractJson.encode {
        ContractJson.strict.encodeToString(AccountLinkRequest.serializer(), request)
    }

    fun parseResponse(payload: String): AccountLinkResponse = ContractJson.parse("account link") {
        ContractJson.tolerant.decodeFromString(AccountLinkResponse.serializer(), payload)
    }
}

/**
 * Whether the stored credential belongs to a linked account. Kept next to the credential in
 * `no_backup`, so it is never restored onto a device that does not hold the credential.
 */
internal interface AccountStore {
    fun linked(): Boolean

    fun setLinked(linked: Boolean)
}

internal class MemoryAccountStore(private var linked: Boolean = false) : AccountStore {
    override fun linked(): Boolean = synchronized(this) { linked }

    override fun setLinked(linked: Boolean): Unit = synchronized(this) { this.linked = linked }
}

internal class FileAccountStore(private val file: File) : AccountStore {
    constructor(context: Context) : this(File(context.noBackupFilesDir, "api-account.txt"))

    override fun linked(): Boolean = synchronized(this) {
        try {
            file.isFile && file.readText() == LINKED
        } catch (_: IOException) {
            false
        }
    }

    override fun setLinked(linked: Boolean): Unit = synchronized(this) {
        try {
            if (linked) file.writeText(LINKED) else file.delete()
        } catch (_: IOException) {
            // Only the label in Settings depends on it; the credential decides the identity.
        }
    }

    private companion object {
        const val LINKED = "account"
    }
}

/** What a link attempt did, in the terms the Settings copy needs. */
internal sealed interface LinkOutcome {
    /**
     * Linked. [switched] is true when this device now continues as an account created on
     * another device; [merged] saved reports moved to it. [stored] is false when the new
     * credential could only be kept in memory.
     */
    data class Linked(val switched: Boolean, val merged: Int, val stored: Boolean) : LinkOutcome

    data object Cancelled : LinkOutcome

    data class Unavailable(val reason: String) : LinkOutcome

    data class Failed(val failure: ApiFailure) : LinkOutcome
}

/**
 * Gets an ID token from [tokens] and links this device's principal with it. When this device
 * continues as an account created on another device, [onSwitched] forgets the checks the
 * revoked guest started here: the server no longer lets this device read them (BC-D07).
 */
internal class AccountLinker(
    private val api: OvrlyApi,
    private val tokens: IdTokenSource,
    private val onSwitched: suspend () -> Unit = {}
) {
    val available: Boolean get() = tokens.available

    suspend fun link(activity: Context? = null): LinkOutcome =
        when (val token = tokens.idToken(activity)) {
            IdTokenResult.Cancelled -> LinkOutcome.Cancelled

            is IdTokenResult.Unavailable -> LinkOutcome.Unavailable(token.reason)

            is IdTokenResult.Token -> when (val result = api.linkAccount(token.value)) {
                is ApiResult.Failure -> LinkOutcome.Failed(result.failure)
                is ApiResult.Success -> result.value.also { if (it.switched) onSwitched() }
            }
        }
}

/**
 * Removes the checks this device holds from the server, with their cached report versions,
 * after it switched to an account created on another device. Shares not yet accepted stay
 * and are sent under the account: their declared or completed upload belonged to the revoked
 * guest, so it is cleared and the staged copy is uploaded again as the account.
 */
@Dao
internal abstract class LocalHistoryDao {
    @Query("DELETE FROM report_versions")
    abstract suspend fun deleteReports()

    @Query("DELETE FROM investigations WHERE server_id IS NOT NULL")
    abstract suspend fun deleteServerChecks()

    @Query(
        "UPDATE investigations SET upload_id = NULL, declared_upload = NULL " +
            "WHERE server_id IS NULL"
    )
    abstract suspend fun forgetGuestUploads()

    @Transaction
    open suspend fun forgetServerHistory() {
        deleteReports()
        deleteServerChecks()
        forgetGuestUploads()
    }
}
