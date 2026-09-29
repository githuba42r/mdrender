package com.a42r.mdrender.cloudpush

import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.common.InputImage

/**
 * Feeds CameraX frames to ML Kit and reports the first QR code it sees.
 *
 * The analyzer fires continuously, so [onScanned] is latched to run at most
 * once: continuing to scan after a hit would fire the callback repeatedly and
 * try to pair several times from one camera view.
 */
class QrPairingScanner(private val onScanned: (String) -> Unit) : ImageAnalysis.Analyzer {

    private val scanner = BarcodeScanning.getClient()
    private var delivered = false

    override fun analyze(imageProxy: ImageProxy) {
        val mediaImage = imageProxy.image
        if (mediaImage == null || delivered) {
            imageProxy.close()
            return
        }
        val image = InputImage.fromMediaImage(mediaImage, imageProxy.imageInfo.rotationDegrees)
        scanner.process(image)
            .addOnSuccessListener { barcodes ->
                if (delivered) return@addOnSuccessListener
                val raw = barcodes.firstOrNull { it.format == Barcode.FORMAT_QR_CODE }?.rawValue
                if (raw != null) {
                    delivered = true
                    onScanned(raw)
                }
            }
            .addOnCompleteListener { imageProxy.close() }
    }
}
