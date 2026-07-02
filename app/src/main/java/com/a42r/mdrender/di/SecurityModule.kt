package com.a42r.mdrender.di

import android.content.Context
import com.a42r.mdrender.security.AppLockManager
import com.a42r.mdrender.security.AuthPreferencesStore
import com.a42r.mdrender.security.CryptoEngine
import com.a42r.mdrender.security.KeystoreManager
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object SecurityModule {

    @Provides
    @Singleton
    fun provideKeystoreManager(): KeystoreManager = KeystoreManager()

    @Provides
    @Singleton
    fun provideCryptoEngine(keystoreManager: KeystoreManager): CryptoEngine = CryptoEngine(keystoreManager)

    @Provides
    @Singleton
    fun provideAppLockManager(): AppLockManager = AppLockManager()

    @Provides
    @Singleton
    fun provideAuthPreferencesStore(@ApplicationContext context: Context): AuthPreferencesStore =
        AuthPreferencesStore(context)
}
