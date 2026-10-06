package app.ovrly

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import app.ovrly.ui.claimLabel

object AppNotifications {
    private const val CHANNEL = "manual_controls"
    private const val RESULTS_CHANNEL = "research_results"
    private const val RESULTS_ID = 103

    fun build(context: Context, title: String, text: String, action: String, target: Class<out Service>,
        actionLabel: String = if (action == "hide") "Hide overlay (capture continues)" else "Stop capture",
    ): Notification {
        context.getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(CHANNEL, "Manual overlay and capture", NotificationManager.IMPORTANCE_LOW)
        )
        val open = PendingIntent.getActivity(context, 0,
            Intent(context, MainActivity::class.java).setAction(MainActivity.ACTION_DETAILS),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val stop = PendingIntent.getService(context, target.name.hashCode(),
            Intent(context, target).setAction(action),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        return NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_ovrly)
            .setContentTitle(title)
            .setContentText(text)
            .setContentIntent(open)
            .setOngoing(true)
            .setSilent(true)
            .addAction(0, actionLabel, stop)
            .build()
    }

    /**
     * Research that continued after Stop has settled: a silent, low-priority notice that opens
     * the capture's report (or the Inbox while its id is unknown). Not shown when the user
     * turned notifications off.
     */
    fun resultsReady(context: Context, claims: Int, investigationId: String?) {
        val manager = context.getSystemService(NotificationManager::class.java)
        if (!manager.areNotificationsEnabled()) return
        manager.createNotificationChannel(
            NotificationChannel(
                RESULTS_CHANNEL,
                "Research results",
                NotificationManager.IMPORTANCE_LOW
            )
        )
        val intent = Intent(context, MainActivity::class.java)
            .setAction(MainActivity.ACTION_OPEN_CHECK)
            .putExtra(MainActivity.EXTRA_INVESTIGATION_ID, investigationId)
        val open = PendingIntent.getActivity(
            context,
            RESULTS_ID,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val notification = NotificationCompat.Builder(context, RESULTS_CHANNEL)
            .setSmallIcon(R.drawable.ic_ovrly)
            .setContentTitle(resultsReadyTitle(claims))
            .setContentText("Research on your examined interval is complete.")
            .setContentIntent(open)
            .setAutoCancel(true)
            .setSilent(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
        manager.notify(RESULTS_ID, notification)
    }

    internal fun resultsReadyTitle(claims: Int) = "Results ready · ${claimLabel(claims)}"
}
