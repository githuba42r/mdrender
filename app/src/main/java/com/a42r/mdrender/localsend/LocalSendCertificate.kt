package com.a42r.mdrender.localsend

import android.content.Context
import org.bouncycastle.asn1.x500.X500Name
import org.bouncycastle.cert.jcajce.JcaX509CertificateConverter
import org.bouncycastle.cert.jcajce.JcaX509v3CertificateBuilder
import org.bouncycastle.operator.jcajce.JcaContentSignerBuilder
import java.io.File
import java.math.BigInteger
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.MessageDigest
import java.security.cert.X509Certificate
import java.util.Date
import javax.net.ssl.KeyManagerFactory

/**
 * Self-signed TLS certificate for the LocalSend HTTPS server, generated once
 * per install. Per the LocalSend protocol the device fingerprint is the
 * SHA-256 hash of this certificate; clients use it to identify devices — they
 * do not chain-validate (all LocalSend certs are self-signed).
 */
class LocalSendCertificate(
    val keyStore: KeyStore,
    val keyManagerFactory: KeyManagerFactory,
    val fingerprint: String
) {
    companion object {
        private const val FILE_NAME = "localsend.p12"
        private const val ALIAS = "localsend"
        // Protects a local file holding a throwaway transport key — not a secret.
        private val PASSWORD = "mdrender-localsend".toCharArray()

        fun getOrCreate(context: Context): LocalSendCertificate {
            val file = File(context.filesDir, FILE_NAME)
            val keyStore = KeyStore.getInstance("PKCS12")
            if (file.exists()) {
                file.inputStream().use { keyStore.load(it, PASSWORD) }
            } else {
                keyStore.load(null, null)
                val keyPair = KeyPairGenerator.getInstance("RSA")
                    .apply { initialize(2048) }.generateKeyPair()
                val name = X500Name("CN=MDRender LocalSend")
                val now = System.currentTimeMillis()
                val builder = JcaX509v3CertificateBuilder(
                    name,
                    BigInteger.valueOf(now),
                    Date(now - DAY_MS),
                    Date(now + 10 * 365 * DAY_MS),
                    name,
                    keyPair.public
                )
                val signer = JcaContentSignerBuilder("SHA256withRSA").build(keyPair.private)
                val cert = JcaX509CertificateConverter().getCertificate(builder.build(signer))
                keyStore.setKeyEntry(ALIAS, keyPair.private, PASSWORD, arrayOf(cert))
                file.outputStream().use { keyStore.store(it, PASSWORD) }
            }

            val cert = keyStore.getCertificate(ALIAS) as X509Certificate
            val fingerprint = MessageDigest.getInstance("SHA-256")
                .digest(cert.encoded)
                .joinToString("") { "%02x".format(it) }
            val kmf = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm())
            kmf.init(keyStore, PASSWORD)
            return LocalSendCertificate(keyStore, kmf, fingerprint)
        }

        private const val DAY_MS = 86_400_000L
    }
}
