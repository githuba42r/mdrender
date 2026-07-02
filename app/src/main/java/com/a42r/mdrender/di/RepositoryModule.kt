package com.a42r.mdrender.di

import com.a42r.mdrender.data.dao.FileDao
import com.a42r.mdrender.data.dao.FolderDao
import com.a42r.mdrender.data.repository.FileRepository
import com.a42r.mdrender.data.repository.FolderRepository
import com.a42r.mdrender.security.CryptoEngine
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object RepositoryModule {

    @Provides
    @Singleton
    fun provideFolderRepository(folderDao: FolderDao): FolderRepository =
        FolderRepository(folderDao)

    @Provides
    @Singleton
    fun provideFileRepository(fileDao: FileDao, cryptoEngine: CryptoEngine): FileRepository =
        FileRepository(fileDao, cryptoEngine)
}
