package app.ovrly.fixtures;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Context;
import android.content.Intent;
import android.content.res.AssetFileDescriptor;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.Bundle;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;
import java.io.File;
import java.io.FileNotFoundException;
import java.io.IOException;
import java.util.List;

/**
 * Serves generated media from the test package, which is a different app and uid from
 * app.ovrly, so every read by the app under test needs a real URI grant. The shell calls
 * {@link #call} to grant or revoke one URI for app.ovrly; nothing else can reach it.
 *
 * <p>URIs are {@code content://AUTHORITY/<length>/<size>/<type>/<subtype>/<file>}: {@code length}
 * is {@code declared} or {@code unknown} (the asset descriptor length), {@code size} is
 * {@code column} or {@code none} (the {@link OpenableColumns#SIZE} value) and
 * {@code type/subtype} is the MIME type the provider claims. Only path characters are used, so
 * the shell passes URIs without quoting.
 *
 * <p>Framework-only Java: this provider runs in the test package's own process, where the
 * Kotlin and AndroidX classes of the app APK are not loaded.
 */
public final class ShareFixtureProvider extends ContentProvider {
    public static final String AUTHORITY = "app.ovrly.test.share-fixtures";
    public static final String GRANT = "grant";
    public static final String REVOKE = "revoke";
    private static final String TARGET_PACKAGE = "app.ovrly";
    private static final String DECLARED = "declared";
    private static final String COLUMN = "column";
    private static final int SEGMENTS = 5;
    private static final int READ = Intent.FLAG_GRANT_READ_URI_PERMISSION;

    @Override
    public boolean onCreate() {
        return true;
    }

    @Override
    public Bundle call(String method, String arg, Bundle extras) {
        Context context = attachedContext();
        Uri uri = Uri.parse(arg);
        if (!AUTHORITY.equals(uri.getAuthority())) {
            throw new IllegalArgumentException("Not a fixture URI");
        }
        if (GRANT.equals(method)) {
            try {
                FixtureMedia.ensure(directory(context));
            } catch (IOException error) {
                throw new IllegalStateException("Could not generate fixtures", error);
            }
            context.grantUriPermission(TARGET_PACKAGE, uri, READ);
        } else if (REVOKE.equals(method)) {
            context.revokeUriPermission(TARGET_PACKAGE, uri, READ);
        } else {
            throw new IllegalArgumentException("Unknown method " + method);
        }
        return Bundle.EMPTY;
    }

    @Override
    public String getType(Uri uri) {
        List<String> segments = segments(uri);
        return segments.get(2) + "/" + segments.get(3);
    }

    @Override
    public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        if (!"r".equals(mode)) {
            throw new SecurityException("Fixtures are read-only");
        }
        return ParcelFileDescriptor.open(file(uri), ParcelFileDescriptor.MODE_READ_ONLY);
    }

    @Override
    public AssetFileDescriptor openAssetFile(Uri uri, String mode) throws FileNotFoundException {
        ParcelFileDescriptor descriptor = openFile(uri, mode);
        long length = DECLARED.equals(segments(uri).get(0))
            ? file(uri).length()
            : AssetFileDescriptor.UNKNOWN_LENGTH;
        return new AssetFileDescriptor(descriptor, 0, length);
    }

    @Override
    public Cursor query(
        Uri uri,
        String[] projection,
        String selection,
        String[] selectionArgs,
        String sortOrder
    ) {
        File file = file(uri);
        MatrixCursor cursor = new MatrixCursor(
            new String[] {OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE}
        );
        boolean known = COLUMN.equals(segments(uri).get(1)) && file.isFile();
        cursor.addRow(new Object[] {file.getName(), known ? file.length() : null});
        return cursor;
    }

    @Override
    public Uri insert(Uri uri, ContentValues values) {
        throw new UnsupportedOperationException("Read-only fixtures");
    }

    @Override
    public int delete(Uri uri, String selection, String[] selectionArgs) {
        throw new UnsupportedOperationException("Read-only fixtures");
    }

    @Override
    public int update(Uri uri, ContentValues values, String selection, String[] selectionArgs) {
        throw new UnsupportedOperationException("Read-only fixtures");
    }

    private static List<String> segments(Uri uri) {
        List<String> segments = uri.getPathSegments();
        if (segments.size() != SEGMENTS) {
            throw new IllegalArgumentException("Unexpected fixture URI shape");
        }
        return segments;
    }

    private File file(Uri uri) {
        String name = segments(uri).get(4);
        if (!FixtureMedia.isKnownName(name)) {
            throw new IllegalArgumentException("Unknown fixture " + name);
        }
        return new File(directory(attachedContext()), name);
    }

    private static File directory(Context context) {
        return new File(context.getFilesDir(), "share-fixtures");
    }

    private Context attachedContext() {
        Context context = getContext();
        if (context == null) {
            throw new IllegalStateException("Provider is not attached");
        }
        return context;
    }
}
