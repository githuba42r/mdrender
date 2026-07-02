package com.a42r.mdrender.security

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AppLockTest {

    @Test
    fun `starts locked with hidden folders not revealed`() {
        val lock = AppLock()
        assertTrue(lock.isLocked.value)
        assertFalse(lock.revealHidden.value)
    }

    @Test
    fun `revealHiddenFolders reveals`() {
        val lock = AppLock()
        lock.unlock()
        lock.revealHiddenFolders()
        assertTrue(lock.revealHidden.value)
    }

    @Test
    fun `going to background re-locks and hides revealed folders`() {
        val lock = AppLock()
        lock.unlock()
        lock.revealHiddenFolders()
        assertTrue(lock.revealHidden.value)

        lock.onBackground()

        assertTrue(lock.isLocked.value)
        assertFalse("reveal must reset on lock", lock.revealHidden.value)
    }

    @Test
    fun `suspendNextLock skips one background lock but not reveal on the following`() {
        val lock = AppLock()
        lock.unlock()
        lock.revealHiddenFolders()

        lock.suspendNextLock()
        lock.onBackground() // skipped
        assertFalse(lock.isLocked.value)
        assertTrue("reveal preserved when lock is suspended", lock.revealHidden.value)

        lock.onBackground() // real
        assertTrue(lock.isLocked.value)
        assertFalse(lock.revealHidden.value)
    }
}
