package com.a42r.mdrender.di

import android.content.Context
import android.content.SharedPreferences
import com.a42r.mdrender.cloudpush.PushServerConfig
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object CloudPushModule {

    @Provides
    @Singleton
    fun provideCloudPushPrefs(@ApplicationContext context: Context): SharedPreferences =
        context.getSharedPreferences(PushServerConfig.PREFS_NAME, Context.MODE_PRIVATE)
}
