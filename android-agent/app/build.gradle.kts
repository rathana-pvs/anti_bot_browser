plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
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
        versionCode = 2
        versionName = "0.2.0-poc"
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

    buildTypes {
        release { isMinifyEnabled = false }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures { buildConfig = true }
}
