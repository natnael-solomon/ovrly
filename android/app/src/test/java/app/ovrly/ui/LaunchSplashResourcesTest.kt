package app.ovrly.ui

import java.io.File
import javax.xml.parsers.DocumentBuilderFactory
import kotlin.math.hypot
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.w3c.dom.Element

class LaunchSplashResourcesTest {
    private val android = "http://schemas.android.com/apk/res/android"

    @Test fun splashKeepsTheSharedTwoGapRingGeometry() {
        val logo = xml("res/drawable/ic_ovrly.xml").elements("path").single()
        val ring = xml("res/drawable/splash_ovrly.xml").elements("group").single { it.attr("name") == "ring" }
        ring.elements("path").forEach {
            assertEquals(logo.attr("pathData"), it.attr("pathData"))
            assertEquals("round", it.attr("strokeLineCap"))
        }
        assertEquals(logo.attr("strokeWidth"), ring.elements("path").first().attr("strokeWidth"))
        assertEquals("#363C3B", logo.attr("strokeColor"))
    }

    @Test fun entireLockupFitsTheNativeIconSafeCircle() {
        val vector = xml("res/drawable/splash_ovrly.xml")
        assertEquals("288dp", vector.attr("width"))
        assertEquals("288dp", vector.attr("height"))
        assertEquals("288", vector.attr("viewportWidth"))
        assertEquals("288", vector.attr("viewportHeight"))
        val ring = vector.elements("group").single { it.attr("name") == "ring" }
        val scale = ring.attr("scaleX").toDouble()
        assertEquals(scale, ring.attr("scaleY").toDouble(), 0.0)
        val centerX = 12 * scale + ring.attr("translateX").toDouble()
        val centerY = 12 * scale + ring.attr("translateY").toDouble()
        val outerRadius = (9.5 + ring.elements("path").first().attr("strokeWidth").toDouble() / 2) * scale
        assertTrue(hypot(centerX - 144, centerY - 144) + outerRadius < 96)

        val wordmark = vector.elements("group").single { it.attr("name") == "wordmark" }
        assertEquals(6, wordmark.elements("path").size)
        // All endpoints and Bezier control points fit; the curves stay in their convex hull.
        val outline = wordmark.elements("path").joinToString("") { it.attr("pathData") }
        val coordinates = Regex("-?\\d+(?:\\.\\d+)?").findAll(outline)
            .map { it.value.toDouble() }.toList()
        assertEquals(0, coordinates.size % 2)
        coordinates.chunked(2).forEach { (x, y) ->
            assertTrue("Wordmark point ($x,$y) is outside the native mask", hypot(x - 144, y - 144) < 96)
            assertTrue("Wordmark must sit below the ring", y > centerY + outerRadius)
        }
    }

    @Test fun launchThemeUsesStaticDarkBrandingAndTheCompatTheme() {
        val resources = xml("res/values/splash.xml")
        val base = resources.elements("style").single { it.getAttribute("name") == "Theme.Ovrly.StartingBase" }
        assertEquals("Theme.SplashScreen", base.getAttribute("parent"))
        val items = base.elements("item").associate { it.getAttribute("name") to it.textContent }
        assertEquals("@drawable/splash_ovrly", items["windowSplashScreenAnimatedIcon"])
        assertEquals("@color/splash_background", items["windowSplashScreenBackground"])
        assertEquals("@style/Theme.Ovrly.Dark", items["postSplashScreenTheme"])
        assertEquals("#080910", resources.elements("color").single().textContent)
        assertFalse(items.containsKey("windowSplashScreenAnimationDuration"))
        assertFalse(items.containsKey("android:windowSplashScreenBrandingImage"))
        val starting = resources.elements("style").single { it.getAttribute("name") == "Theme.Ovrly.Starting" }
        assertEquals("Theme.Ovrly.StartingBase", starting.getAttribute("parent"))
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
        assertEquals("@drawable/ic_ovrly", manifest.elements("application").single().attr("icon"))
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
