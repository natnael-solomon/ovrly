package app.ovrly.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SampleReportsTest {
    @Test fun samplesHaveStableUniqueIdsAndExplicitDisclosures() {
        assertEquals(SampleReports.size, SampleReports.map { it.id }.distinct().size)
        SampleReports.forEach { report ->
            assertTrue(report.claims.isNotEmpty())
            assertTrue(report.artwork in 0..3)
            report.claims.forEach {
                assertTrue(it.label.endsWith("/ sample"))
                assertTrue(it.explanation.isNotBlank())
            }
        }
    }

    @Test fun searchIgnoresCaseAndSurroundingWhitespace() {
        assertEquals(listOf("outside"), filterSamples(SampleReports, " OUTSIDE ").map { it.id })
        assertEquals(listOf("outside", "rest"), filterSamples(SampleReports, "health").map { it.id })
    }

    @Test fun topicsAndSearchCombineWithoutInventingResults() {
        assertEquals(listOf("shade"), filterSamples(SampleReports, "shade", "Environment").map { it.id })
        assertTrue(filterSamples(SampleReports, "shade", "Health").isEmpty())
        assertTrue(filterSamples(SampleReports, "unavailable").isEmpty())
        assertEquals(SampleReports, filterSamples(SampleReports, ""))
    }

    @Test fun topicsOnlyContainAvailableSamples() {
        assertEquals("All", SampleTopics.first())
        SampleTopics.drop(1).forEach { assertTrue(filterSamples(SampleReports, "", it).isNotEmpty()) }
    }

    @Test fun chromeIsTheFreshInstallDefault() {
        assertTrue(AppearanceStore.DEFAULT_DARK)
    }
}
