package com.a42r.mdrender.security

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AppLockTest {

    @Test
    fun `starts unlocked with hidden folders not revealed`() {
        val lock = AppLock()
        assertFalse("app should start unlocked", lock.isLocked.value)
        assertFalse(lock.revealHidden.value)
    }

    @Test
    fun `revealHiddenFolders reveals`() {
        val lock = AppLock()
        lock.revealHiddenFolders()
        assertTrue(lock.revealHidden.value)
    }

    @Test
    fun `hideHiddenFolders turns reveal off`() {
        val lock = AppLock()
        lock.revealHiddenFolders()
        assertTrue(lock.revealHidden.value)

        lock.hideHiddenFolders()

        assertFalse(lock.revealHidden.value)
    }

    @Test
    fun `going to background clears reveal but does not lock`() {
        val lock = AppLock()
        lock.revealHiddenFolders()
        assertTrue(lock.revealHidden.value)

        lock.onBackground()

        assertFalse("reveal must reset on background", lock.revealHidden.value)
        assertFalse("app must not re-lock on background", lock.isLocked.value)
    }
}
