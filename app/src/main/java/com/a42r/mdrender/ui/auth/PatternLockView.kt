package com.a42r.mdrender.ui.auth

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.layout.*
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.unit.dp
import kotlin.math.sqrt
import androidx.compose.ui.text.style.TextAlign

private data class Dot(val row: Int, val col: Int, val center: Offset)

@Composable
fun PatternLockView(
    onPatternComplete: (List<Int>) -> Unit,
    errorMessage: String? = null,
    modifier: Modifier = Modifier
) {
    val dotRadius = 24.dp
    val lineWidth = 4.dp
    var selectedDots by remember { mutableStateOf(listOf<Int>()) }
    var currentDrag by remember { mutableStateOf<Offset?>(null) }
    val dotCount = 3

    // Calculate dot positions — same as canvas size accounting for padding
    var canvasSize by remember { mutableStateOf(300f) }

    Box(
        modifier = modifier.fillMaxWidth().aspectRatio(1f),
        contentAlignment = Alignment.Center
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            // Error text
            if (errorMessage != null) {
                Text(
                    text = errorMessage,
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodyMedium,
                    textAlign = TextAlign.Center,
                    modifier = Modifier.padding(bottom = 16.dp)
                )
            }

            val primaryColor = MaterialTheme.colorScheme.primary

            Canvas(
                modifier = Modifier
                    .fillMaxWidth()
                    .aspectRatio(1f)
                    .padding(32.dp)
                    .pointerInput(Unit) {
                        detectDragGestures(
                            onDragStart = { offset ->
                                val dot = hitTestDot(offset, canvasSize, dotCount)
                                if (dot != -1 && dot !in selectedDots) {
                                    selectedDots = selectedDots + dot
                                }
                            },
                            onDrag = { change, _ ->
                                change.consume()
                                currentDrag = change.position
                                val dot = hitTestDot(change.position, canvasSize, dotCount)
                                if (dot != -1 && dot !in selectedDots) {
                                    selectedDots = selectedDots + dot
                                }
                            },
                            onDragEnd = {
                                onPatternComplete(selectedDots)
                                selectedDots = emptyList()
                                currentDrag = null
                            },
                            onDragCancel = {
                                selectedDots = emptyList()
                                currentDrag = null
                            }
                        )
                    }
            ) {
                canvasSize = size.width
                val spacing = canvasSize / (dotCount + 1)
                val dots = (0 until dotCount).flatMap { row ->
                    (0 until dotCount).map { col ->
                        val center = Offset(spacing * (col + 1), spacing * (row + 1))
                        Dot(row, col, center)
                    }
                }

                // Draw lines between selected dots
                for (i in 0 until selectedDots.size - 1) {
                    val from = dots[selectedDots[i]].center
                    val to = dots[selectedDots[i + 1]].center
                    drawLine(primaryColor, from, to, lineWidth.toPx(), cap = StrokeCap.Round)
                }
                // Line to current drag position
                if (selectedDots.isNotEmpty() && currentDrag != null) {
                    drawLine(primaryColor, dots[selectedDots.last()].center, currentDrag!!, lineWidth.toPx(), cap = StrokeCap.Round)
                }

                // Draw dots
                for ((index, dot) in dots.withIndex()) {
                    val isSelected = index in selectedDots
                    drawCircle(
                        color = if (isSelected) primaryColor else Color.Gray,
                        radius = dotRadius.toPx(),
                        center = dot.center
                    )
                    if (isSelected) {
                        drawCircle(
                            color = primaryColor.copy(alpha = 0.3f),
                            radius = dotRadius.toPx() + 8f,
                            center = dot.center,
                            style = Stroke(width = 2f)
                        )
                    }
                }
            }

            Text(
                text = "Draw your pattern",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 8.dp)
            )
        }
    }
}

private fun hitTestDot(offset: Offset, canvasSize: Float, dotCount: Int): Int {
    val spacing = canvasSize / (dotCount + 1)
    val hitRadius = canvasSize / (dotCount * 4f)
    var bestIndex = -1
    var bestDist = Float.MAX_VALUE
    for (row in 0 until dotCount) {
        for (col in 0 until dotCount) {
            val center = Offset(spacing * (col + 1), spacing * (row + 1))
            val dist = sqrt((offset.x - center.x) * (offset.x - center.x) + (offset.y - center.y) * (offset.y - center.y))
            if (dist < hitRadius && dist < bestDist) {
                bestDist = dist
                bestIndex = row * dotCount + col
            }
        }
    }
    return bestIndex
}
