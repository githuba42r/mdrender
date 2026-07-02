package com.a42r.mdrender.security

import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.runTest
import org.junit.Assert.*
import org.junit.Test

class AppLockManagerTest {

    @Test
    fun initiallyLocked() = runTest {
        val manager = AppLockManager()
        assertTrue(manager.isLocked.first())
    }

    @Test
    fun unlockThenLock() = runTest {
        val manager = AppLockManager()
        manager.unlock()
        assertFalse(manager.isLocked.first())
        manager.lock()
        assertTrue(manager.isLocked.first())
    }

    @Test
    fun failedAttempts_causeLockout() = runTest {
        val manager = AppLockManager()
        (1..5).forEach {
            val lockedOut = manager.recordFailedAttempt()
            if (it < 5) assertFalse(lockedOut) else assertTrue(lockedOut)
        }
        assertTrue(manager.isLockedOut())
    }

    @Test
    fun unlock_resetsFailuresAndLockout() = runTest {
        val manager = AppLockManager()
        repeat(5) { manager.recordFailedAttempt() }
        manager.unlock()
        assertFalse(manager.isLockedOut())
    }
}
