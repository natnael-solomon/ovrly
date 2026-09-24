package app.ovrly.ui

import java.io.File
import javax.xml.parsers.DocumentBuilderFactory
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.w3c.dom.Element

class LaunchSplashResourcesTest {
    private val android = "http://schemas.android.com/apk/res/android"

    private val densities = mapOf("mdpi" to 1.0, "hdpi" to 1.5, "xhdpi" to 2.0, "xxhdpi" to 3.0, "xxxhdpi" to 4.0)

    /** Reads the WebP VP8X canvas header: pixel QA lives in scripts/splash_icon.py, which produces these files. */
    private fun webpCanvas(file: File): Triple<Int, Int, Boolean> {
        val h = file.inputStream().use { it.readNBytes(30) }
        assertEquals("RIFF", String(h, 0, 4, Charsets.US_ASCII))
        assertEquals("WEBP", String(h, 8, 4, Charsets.US_ASCII))
        assertEquals("extended WebP (alpha-capable)", "VP8X", String(h, 12, 4, Charsets.US_ASCII))
        fun u24(at: Int) = (h[at].toInt() and 0xFF) or (h[at + 1].toInt() and 0xFF shl 8) or (h[at + 2].toInt() and 0xFF shl 16)
        return Triple(u24(24) + 1, u24(27) + 1, h[20].toInt() and 0x10 != 0)
    }

    /** Asserts a square or fixed-height bitmap ships at every density with alpha and a size cap. */
    private fun bitmapAtEveryDensity(name: String, folder: String, heightDp: Int, maxKb: Int, widthDp: Int? = heightDp) {
        densities.forEach { (density, scale) ->
            val file = File("src/main/res/$folder-$density/$name.webp")
            assertTrue("$density $name missing", file.isFile)
            val (width, height, alpha) = webpCanvas(file)
            assertEquals("$density $name height", Math.round(heightDp * scale).toInt(), height)
            if (widthDp != null) assertEquals("$density $name width", Math.round(widthDp * scale).toInt(), width)
            assertTrue("$density $name needs alpha", alpha)
            assertTrue("$density $name too large: ${file.length()} bytes", file.length() < maxKb * 1024)
        }
    }

    @Test fun splashIsABitmapOnTheNativeIconCanvasAtEveryDensity() {
        assertFalse("bitmap splash replaces the vector", File("src/main/res/drawable/splash_ovrly.xml").exists())
        // 288dp canvas: Android shows only the central 192dp under the icon mask.
        bitmapAtEveryDensity("splash_ovrly", "drawable", heightDp = 288, maxKb = 150)
    }

    @Test fun notificationIconKeepsItsThinMonochromeGeometry() {
        val logo = xml("res/drawable/ic_ovrly.xml").elements("path").single()
        assertEquals(
            "M16.75,20.227241 A9.5,9.5 0,1 1,16.75 3.772759 M20.927080,8.750809 A9.5,9.5 0,0 1,20.927080 15.249191",
            logo.attr("pathData"),
        )
        assertEquals("#363C3B", logo.attr("strokeColor"))
    }

    @Test fun headerWordmarkBitmapShipsAtEveryDensity() {
        bitmapAtEveryDensity("wordmark_ovrly", "drawable", heightDp = 40, maxKb = 40, widthDp = null)
        densities.forEach { (density, _) ->
            val (width, height, _) = webpCanvas(File("src/main/res/drawable-$density/wordmark_ovrly.webp"))
            assertTrue("$density keeps the wordmark's ~2.1 aspect", width.toDouble() / height in 2.0..2.3)
        }
    }

    @Test fun launcherIsAnAdaptiveIconWithTheChromeRingForeground() {
        val icon = xml("res/mipmap-anydpi/ic_launcher.xml")
        assertEquals("adaptive-icon", icon.tagName)
        assertEquals("@drawable/ic_launcher_background", icon.elements("background").single().attr("drawable"))
        assertEquals("@mipmap/ic_launcher_foreground", icon.elements("foreground").single().attr("drawable"))
        assertEquals("@drawable/ic_launcher_monochrome", icon.elements("monochrome").single().attr("drawable"))

        // Foreground bitmaps: 108dp canvas at every density with alpha.
        bitmapAtEveryDensity("ic_launcher_foreground", "mipmap", heightDp = 108, maxKb = 40)

        // Background is a full-bleed 108dp vector; monochrome is the ring inside the safe zone.
        val background = xml("res/drawable/ic_launcher_background.xml")
        assertEquals("108dp", background.attr("width"))
        assertTrue(background.elements("path").all { it.attr("pathData") == "M0,0h108v108h-108z" })
        val mono = xml("res/drawable/ic_launcher_monochrome.xml")
        assertEquals("108dp", mono.attr("width"))
        val group = mono.elements("group").single()
        val scale = group.attr("scaleX").toDouble()
        val outer = (9.5 + 2.6 / 2) * scale
        val center = 12 * scale + group.attr("translateX").toDouble()
        assertEquals(54.0, center, 0.0)
        assertTrue("monochrome ring must stay inside the 66dp safe zone", outer * 2 <= 66)
        assertEquals(xml("res/drawable/ic_ovrly.xml").elements("path").single().attr("pathData"),
            mono.elements("path").single().attr("pathData"))
    }

    @Test fun launchThemeUsesStaticDarkBrandingAndTheCompatTheme() {
        val resources = xml("res/values/splash.xml")
        val base = resources.elements("style").single { it.getAttribute("name") == "Theme.Ovrly.StartingBase" }
        assertEquals("Theme.SplashScreen", base.getAttribute("parent"))
        val items = base.elements("item").associate { it.getAttribute("name") to it.textContent }
        assertEquals("@drawable/splash_ovrly", items["windowSplashScreenAnimatedIcon"])
        assertEquals("@color/splash_background", items["windowSplashScreenBackground"])
        assertEquals("@style/Theme.Ovrly.Dark", items["postSplashScreenTheme"])
        val colors = resources.elements("color").associate { it.getAttribute("name") to it.textContent }
        assertEquals("#080910", colors["splash_background"])
        assertFalse(items.containsKey("windowSplashScreenAnimationDuration"))
        assertFalse(items.containsKey("android:windowSplashScreenBrandingImage"))
        val starting = resources.elements("style").single { it.getAttribute("name") == "Theme.Ovrly.Starting" }
        assertEquals("Theme.Ovrly.StartingBase", starting.getAttribute("parent"))
    }

    @Test fun lightSplashVariantMatchesThePaperThemeAndKeepsTheIcon() {
        val resources = xml("res/values/splash.xml")
        val light = resources.elements("style").single { it.getAttribute("name") == "Theme.Ovrly.Starting.Light" }
        // Implicit dotted parent: inherits Theme.Ovrly.Starting, and with it the v33 icon_preferred.
        assertFalse(light.hasAttribute("parent"))
        val items = light.elements("item").associate { it.getAttribute("name") to it.textContent }
        assertEquals("@color/splash_background_light", items["windowSplashScreenBackground"])
        assertEquals("@style/Theme.Ovrly", items["postSplashScreenTheme"])
        assertEquals("true", items["android:windowLightStatusBar"])
        assertEquals("true", items["android:windowLightNavigationBar"])
        assertFalse("icon comes from the base theme", items.containsKey("windowSplashScreenAnimatedIcon"))
        val colors = resources.elements("color").associate { it.getAttribute("name") to it.textContent }
        assertEquals("#F0EFE5", colors["splash_background_light"])
        // Must match the Light app theme's window background so the handoff has no colour step.
        val appTheme = xml("res/values/styles.xml").elements("style").single { it.getAttribute("name") == "Theme.Ovrly" }
        assertEquals(colors["splash_background_light"],
            appTheme.elements("item").single { it.getAttribute("name") == "android:windowBackground" }.textContent)
    }

    @Test fun android13PrefersTheIconWithoutReplacingTheBaseTheme() {
        val theme = xml("res/values-v33/splash.xml").elements("style").single()
        assertEquals("Theme.Ovrly.Starting", theme.getAttribute("name"))
        assertEquals("Theme.Ovrly.StartingBase", theme.getAttribute("parent"))
        val item = theme.elements("item").single()
        assertEquals("android:windowSplashScreenBehavior", item.getAttribute("name"))
        assertEquals("icon_preferred", item.textContent)
    }

    @Test fun splashIsOnTheExistingActivityNotTheApplicationIcon() {
        val manifest = xml("AndroidManifest.xml")
        val activity = manifest.elements("activity").single()
        assertEquals(".MainActivity", activity.attr("name"))
        assertEquals("@style/Theme.Ovrly.Starting", activity.attr("theme"))
        assertEquals("singleTop", activity.attr("launchMode"))
        val application = manifest.elements("application").single()
        assertEquals("@mipmap/ic_launcher", application.attr("icon"))
        assertEquals("@mipmap/ic_launcher", application.attr("roundIcon"))
        assertTrue(activity.elements("action").any { it.attr("name") == "android.intent.action.SEND" })
    }

    private fun xml(path: String): Element = DocumentBuilderFactory.newInstance().apply {
        isNamespaceAware = true
        setFeature("http://apache.org/xml/features/disallow-doctype-decl", true)
    }.newDocumentBuilder().parse(File("src/main", path)).documentElement

    private fun Element.attr(name: String): String = getAttributeNS(android, name)

    private fun Element.elements(name: String): List<Element> =
        getElementsByTagName(name).let { nodes -> (0 until nodes.length).map { nodes.item(it) as Element } }
}
