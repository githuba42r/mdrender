package com.a42r.mdrender.ui.auth

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp

@Composable
fun PinEntryScreen(
    onSubmit: (String) -> Boolean,
    errorMessage: String? = null,
    modifier: Modifier = Modifier
) {
    var pin by remember { mutableStateOf("") }

    Column(
        modifier = modifier.fillMaxSize().padding(32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center
    ) {
        Text("Enter PIN", style = MaterialTheme.typography.headlineMedium)

        Spacer(Modifier.height(24.dp))

        // PIN dots display
        Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
            repeat(8) { index ->
                val filled = index < pin.length
                Surface(
                    shape = MaterialTheme.shapes.small,
                    color = if (filled) MaterialTheme.colorScheme.primary
                            else MaterialTheme.colorScheme.surfaceVariant,
                    modifier = Modifier.size(20.dp)
                ) {}
            }
        }

        Spacer(Modifier.height(24.dp))

        // Numeric keypad
        val keys = listOf(
            listOf("1", "2", "3"),
            listOf("4", "5", "6"),
            listOf("7", "8", "9"),
            listOf("", "0", "⌫")
        )

        for (row in keys) {
            Row(horizontalArrangement = Arrangement.Center) {
                for (key in row) {
                    when {
                        key.isEmpty() -> Spacer(Modifier.size(72.dp))
                        key == "⌫" -> TextButton(
                            onClick = { if (pin.isNotEmpty()) pin = pin.dropLast(1) },
                            modifier = Modifier.size(72.dp)
                        ) { Text("⌫") }
                        else -> TextButton(
                            onClick = {
                                if (pin.length < 8) {
                                    pin += key
                                }
                            },
                            modifier = Modifier.size(72.dp)
                        ) { Text(key, style = MaterialTheme.typography.headlineSmall) }
                    }
                }
            }
        }

        Spacer(Modifier.height(16.dp))

        if (errorMessage != null) {
            Text(errorMessage, color = MaterialTheme.colorScheme.error)
            Spacer(Modifier.height(8.dp))
        }

        Button(
            onClick = {
                onSubmit(pin)
                pin = ""
            },
            enabled = pin.length >= 4
        ) {
            Text("Unlock")
        }
    }
}
