import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Release signing key, kept out of git. Every release must use the same key, or Android refuses
// to install an update over the app already on the phone. Read from keystore.properties next to
// this project (storeFile, storePassword, keyAlias, keyPassword) or from AGENT_KEYSTORE_* variables.
val signing = Properties().apply {
    rootProject.file("keystore.properties").takeIf { it.isFile }?.inputStream()?.use { load(it) }
    mapOf("storeFile" to "AGENT_KEYSTORE_FILE", "storePassword" to "AGENT_KEYSTORE_PASSWORD",
          "keyAlias" to "AGENT_KEY_ALIAS", "keyPassword" to "AGENT_KEY_PASSWORD")
        .forEach { (key, env) -> System.getenv(env)?.let { setProperty(key, it) } }
}

android {
    namespace = "com.isolatedbrowser.agent"
    compileSdk = 35
    ndkVersion = "27.2.12479018"

    defaultConfig {
        applicationId = "com.isolatedbrowser.agent"
        // excludeRoute() for the proxy endpoint needs API 33.
        minSdk = 33
        // Below 34 so the VPN service needs no foreground-service type declaration yet.
        targetSdk = 33
        versionCode = 4
        versionName = "0.3.0"
        ndk { abiFilters += listOf("arm64-v8a") }
        externalNativeBuild {
            ndkBuild {
                arguments += listOf(
                    "APP_PLATFORM=android-33",
                    "APP_CFLAGS+=-DPKGNAME=com/isolatedbrowser/agent -DCLSNAME=TunnelEngine",
                )
            }
        }
    }

    externalNativeBuild {
        ndkBuild { path = file("src/main/jni/Android.mk") }
    }

    signingConfigs {
        if (signing.getProperty("storeFile") != null) {
            create("release") {
                storeFile = rootProject.file(signing.getProperty("storeFile"))
                storePassword = signing.getProperty("storePassword")
                keyAlias = signing.getProperty("keyAlias")
                keyPassword = signing.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        // Not debuggable, so `run-as` cannot read the app's files over ADB.
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.findByName("release")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures { buildConfig = true }
}
