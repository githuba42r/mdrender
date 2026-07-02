package com.a42r.mdrender.data.repository

import android.util.Base64
import com.a42r.mdrender.security.AuthPreferencesStore
import org.junit.Assert.*
import org.junit.Test
import org.mockito.ArgumentMatchers.anyInt
import org.mockito.Mockito.doAnswer
import org.mockito.Mockito.mockStatic
import org.mockito.kotlin.any
import org.mockito.kotlin.mock
import org.mockito.stubbing.Answer
import java.security.MessageDigest
import java.security.SecureRandom
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.PBEKeySpec

class AuthRepositoryTest {

    @Test
    fun patternVerification_matchesStoredPattern() {
        mockStatic(Base64::class.java).use { mockedBase64 ->
            mockedBase64.`when`<String> { Base64.encodeToString(any(), anyInt()) }
                .thenAnswer { inv ->
                    java.util.Base64.getEncoder().encodeToString(inv.getArgument(0))
                }

            val expectedHash = java.util.Base64.getEncoder().encodeToString(
                MessageDigest.getInstance("SHA-256")
                    .digest("0,1,2,5,8".toByteArray(Charsets.UTF_8))
            )

            val prefs = mock<AuthPreferencesStore>()
            doAnswer(Answer<String?> { expectedHash }).`when`(prefs).patternHash

            val repo = AuthRepository(prefs, mock())
            assertTrue(repo.verifyPattern(listOf(0, 1, 2, 5, 8)))
            assertFalse(repo.verifyPattern(listOf(0, 1, 2, 5)))
        }
    }

    @Test
    fun pinVerification_matchesStoredPin() {
        mockStatic(Base64::class.java).use { mockedBase64 ->
            mockedBase64.`when`<String> { Base64.encodeToString(any(), anyInt()) }
                .thenAnswer { inv ->
                    java.util.Base64.getEncoder().encodeToString(inv.getArgument(0))
                }
            mockedBase64.`when`<ByteArray> { Base64.decode(any<String>(), anyInt()) }
                .thenAnswer { inv ->
                    java.util.Base64.getDecoder().decode(inv.getArgument<String>(0))
                }

            val salt = ByteArray(16).also { SecureRandom().nextBytes(it) }
            val spec = PBEKeySpec("1234".toCharArray(), salt, 100_000, 256)
            val factory = SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256")
            val expectedHash = java.util.Base64.getEncoder().encodeToString(
                factory.generateSecret(spec).encoded
            )
            val saltStr = java.util.Base64.getEncoder().encodeToString(salt)

            val prefs = mock<AuthPreferencesStore>()
            doAnswer(Answer<String?> { expectedHash }).`when`(prefs).pinHash
            doAnswer(Answer<String?> { saltStr }).`when`(prefs).pinSalt

            val repo = AuthRepository(prefs, mock())
            assertTrue(repo.verifyPin("1234"))
            assertFalse(repo.verifyPin("1235"))
        }
    }
}
