package app.ovrly

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat

object AppNotifications {
    private const val CHANNEL = "manual_controls"

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
}
