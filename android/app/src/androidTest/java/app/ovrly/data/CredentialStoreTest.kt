package app.ovrly.data

import androidx.test.ext.junit.runners.AndroidJUnit4
import app.ovrly.testing.Device
import java.io.File
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

/**
 * The guest credential store on a real Android Keystore (AN-10, #36): the token is encrypted
 * at rest in `no_backup`, an account credential replaces it in place, and a damaged file is
 * dropped instead of being read. Uses its own files, never the app's stored credential.
 */
@RunWith(AndroidJUnit4::class)
class CredentialStoreTest {
    private val directory = Device.context.noBackupFilesDir
    private val file = File(directory, "test-credential.bin")
    private val temporary = File(directory, "test-credential.bin.tmp")
    private val accountFile = File(directory, "test-account.txt")

    @After
    fun cleanUp() {
        file.delete()
        temporary.delete()
        accountFile.delete()
    }

    @Test
    fun theGuestCredentialIsEncryptedInNoBackupStorage() {
        val token = "ovk_synthetic_guest_token_0001"
        val store = KeystoreCredentialStore(file)
        assertNull(store.read())
        assertTrue(store.write(token))

        assertTrue(file.isFile)
        assertEquals(Device.context.noBackupFilesDir, file.parentFile)
        assertFalse(String(file.readBytes(), Charsets.ISO_8859_1).contains(token))
        // A new instance reads it through the Keystore key, as after a process restart.
        assertEquals(token, KeystoreCredentialStore(file).read())
    }

    @Test
    fun anAccountCredentialReplacesTheGuestOneInPlace() {
        val store = KeystoreCredentialStore(file)
        assertTrue(store.write("ovk_synthetic_guest_token_0002"))
        assertTrue(store.write("ovk_synthetic_account_token_0002"))
        assertEquals("ovk_synthetic_account_token_0002", KeystoreCredentialStore(file).read())
        assertFalse(temporary.exists())
        store.clear()
        assertNull(store.read())
        assertFalse(file.exists())
    }

    @Test
    fun aDamagedFileIsDroppedSoANewGuestCanBeMinted() {
        file.writeBytes(ByteArray(40) { it.toByte() })
        assertNull(KeystoreCredentialStore(file).read())
        assertFalse(file.exists())
        file.writeBytes(ByteArray(3))
        assertNull(KeystoreCredentialStore(file).read())
        assertFalse(file.exists())
    }

    @Test
    fun theLinkedFlagLivesNextToTheCredential() {
        val account = FileAccountStore(accountFile)
        assertFalse(account.linked())
        account.setLinked(true)
        assertTrue(FileAccountStore(accountFile).linked())
        account.setLinked(false)
        assertFalse(accountFile.exists())
        assertFalse(FileAccountStore(accountFile).linked())
    }
}
