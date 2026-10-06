package app.ovrly.data

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.io.File
import java.io.IOException
import java.security.GeneralSecurityException
import java.security.KeyStore
import java.security.ProviderException
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Where the opaque guest bearer credential lives. Implementations never log it. */
internal interface CredentialStore {
    fun read(): String?

    /** Persists [token]; false means it could not be stored and lives only in memory. */
    fun write(token: String): Boolean

    fun clear()
}

/** Process-memory store, for tests and as the fallback when persistence fails. */
internal class MemoryCredentialStore(private var token: String? = null) : CredentialStore {
    override fun read(): String? = synchronized(this) { token }

    override fun write(token: String): Boolean = synchronized(this) {
        this.token = token
        true
    }

    override fun clear(): Unit = synchronized(this) { token = null }
}

/**
 * Keeps the credential in app-private `no_backup` storage, encrypted with an AES-GCM key held
 * by the Android Keystore, so it is neither backed up, transferred to another device nor
 * readable from a file copy. An unreadable file is deleted and treated as absent: the next
 * call mints a new guest identity. After an account link on a second device (AN-10, #36;
 * BC-D07) the account's credential replaces the guest one in the same file, in one rename.
 */
internal class KeystoreCredentialStore(private val file: File) : CredentialStore {
    constructor(context: Context) : this(File(context.noBackupFilesDir, "api-credential.bin"))

    override fun read(): String? = synchronized(this) {
        if (!file.isFile) return@synchronized null
        try {
            val stored = file.readBytes()
            val cipher = Cipher.getInstance(TRANSFORMATION)
            val iv = stored.copyOfRange(0, IV_BYTES)
            cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(TAG_BITS, iv))
            String(cipher.doFinal(stored, IV_BYTES, stored.size - IV_BYTES), Charsets.UTF_8)
        } catch (_: GeneralSecurityException) {
            discard()
        } catch (_: IOException) {
            discard()
        } catch (_: ProviderException) {
            discard()
        } catch (_: IndexOutOfBoundsException) {
            discard()
        }
    }

    /** Returns false when the credential could not be persisted; it is then used in memory only. */
    override fun write(token: String): Boolean = synchronized(this) {
        val temporary = File(file.parentFile, "${file.name}.tmp")
        try {
            val cipher = Cipher.getInstance(TRANSFORMATION)
            cipher.init(Cipher.ENCRYPT_MODE, key())
            temporary.writeBytes(cipher.iv + cipher.doFinal(token.toByteArray(Charsets.UTF_8)))
            temporary.renameTo(file).also { stored -> if (!stored) temporary.delete() }
        } catch (_: GeneralSecurityException) {
            temporary.delete()
            false
        } catch (_: IOException) {
            temporary.delete()
            false
        } catch (_: ProviderException) {
            temporary.delete()
            false
        }
    }

    override fun clear(): Unit = synchronized(this) { file.delete() }

    private fun discard(): String? {
        file.delete()
        return null
    }

    private fun key(): SecretKey {
        val keyStore = KeyStore.getInstance(KEYSTORE).apply { load(null) }
        return keyStore.getKey(ALIAS, null) as? SecretKey ?: KeyGenerator
            .getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE)
            .apply {
                init(
                    KeyGenParameterSpec.Builder(
                        ALIAS,
                        KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT
                    )
                        .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                        .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                        .setKeySize(KEY_BITS)
                        .build()
                )
            }
            .generateKey()
    }

    private companion object {
        const val KEYSTORE = "AndroidKeyStore"
        const val ALIAS = "ovrly-api-credential"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
        const val IV_BYTES = 12
        const val TAG_BITS = 128
        const val KEY_BITS = 256
    }
}
