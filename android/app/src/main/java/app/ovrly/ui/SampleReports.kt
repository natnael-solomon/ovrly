package app.ovrly.ui

internal data class SampleReport(
    val id: String,
    val title: String,
    val topic: String,
    val artwork: Int,
    val claims: List<DemoClaim>,
)

internal val SampleReports = listOf(
    SampleReport("outside", "A moment outside", "Health", 0, DemoClaims),
    SampleReport("shade", "Urban shade", "Environment", 1, listOf(
        DemoClaim("00:08", "Tree cover can change how a street feels.", "Context / sample",
            "An example of a claim that needs a place, a measurement and a comparison. No temperature data or source was retrieved."),
        DemoClaim("00:24", "Every city needs the same solution.", "Mixed / sample",
            "This sample shows where local climate, water use and street design would need to be considered. It is not a completed assessment."),
    )),
    SampleReport("rest", "Rest & routine", "Health", 2, listOf(
        DemoClaim("00:14", "One routine works for everyone.", "Mixed / sample",
            "An illustrative qualification, not health advice. A real review would need the population, study methods and limits."),
        DemoClaim("00:39", "This is my favorite way to end the day.", "Opinion / sample",
            "A personal preference is not a factual verdict. This is a fixed example, not an analyzed video."),
    )),
    SampleReport("attention", "The attention economy", "Technology", 3, listOf(
        DemoClaim("00:06", "Every notification changes our focus.", "Context / sample",
            "This illustrates a statement that needs definitions and supporting research. No research has been performed."),
        DemoClaim("00:31", "Turning alerts off solved it for me.", "Opinion / sample",
            "This is a sample personal account. It does not establish the same result for other people."),
    )),
)

internal val SampleTopics = listOf("All") + SampleReports.map { it.topic }.distinct()

internal fun filterSamples(
    reports: List<SampleReport>,
    query: String,
    topic: String = "All",
): List<SampleReport> {
    val term = query.trim()
    return reports.filter {
        (topic == "All" || it.topic == topic) &&
            (it.title.contains(term, ignoreCase = true) || it.topic.contains(term, ignoreCase = true))
    }
}
