import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

#: Folder created inside the device's public Downloads directory.
DOWNLOAD_SUBDIR = "hako2epub"

EPUB_MIME = "application/epub+zip"

_WRITE_EXTERNAL_STORAGE = "android.permission.WRITE_EXTERNAL_STORAGE"

#: Chunk size for streaming through a Java buffer.
_COPY_BUFFER = 256 * 1024


def sdk_int():
    try:
        from android.os import Build

        return int(Build.VERSION.SDK_INT)
    except Exception:
        return None


def is_android():
    return sdk_int() is not None


def _context():
    import toga

    return toga.App.app._impl.native.getApplicationContext()


def public_downloads_dir():
    try:
        from android.os import Environment

        native = Environment.getExternalStoragePublicDirectory(
            Environment.DIRECTORY_DOWNLOADS
        )
        if native is None:
            return None
        return Path(native.getAbsolutePath())
    except Exception:
        return None


def has_legacy_write_permission():
    try:
        import toga
        from android.content.pm import PackageManager

        granted = toga.App.app._impl._native_checkSelfPermission(
            _WRITE_EXTERNAL_STORAGE
        )
        return granted == PackageManager.PERMISSION_GRANTED
    except Exception:
        return False


def request_legacy_write_permission(on_complete):
    try:
        import toga

        toga.App.app._impl.request_permissions(
            [_WRITE_EXTERNAL_STORAGE],
            lambda permissions, grants: on_complete(bool(grants) and all(grants)),
        )
    except Exception:
        on_complete(False)


def has_all_files_access():
    sdk = sdk_int()
    if sdk is not None and sdk < 30:
        return False
    try:
        from android.os import Environment

        return bool(Environment.isExternalStorageManager())
    except Exception:
        return False


def can_request_all_files_access():
    sdk = sdk_int()
    return sdk is None or sdk >= 30


def request_all_files_access():
    try:
        from android.content import Intent
        from android.net import Uri
        from android.provider import Settings

        import toga

        activity = toga.App.app._impl.native
        package = activity.getPackageName()
    except Exception as e:
        return False, f"Android APIs unavailable: {e}"

    attempts = (
        ("this app's toggle", Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION, True),
        ("the app list", Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION, False),
    )

    problems = []
    for description, action, per_app in attempts:
        try:
            intent = Intent(action)
            if per_app:
                intent.setData(Uri.parse(f"package:{package}"))
            activity.startActivity(intent)
            return True, description
        except Exception as e:
            logger.warning(f"Could not open {action}: {e}")
            problems.append(f"{description}: {e}")

    return False, "; ".join(problems)


def ensure_public_folder():
    downloads = public_downloads_dir()
    if downloads is None:
        return None

    target = downloads / DOWNLOAD_SUBDIR
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None

    probe = target / ".hako2epub-write-test"
    try:
        with open(probe, "wb") as f:
            f.write(b"")
    except OSError:
        return None
    finally:
        try:
            os.unlink(probe)
        except OSError:
            pass

    return target


def _java_int(value):
    try:
        from java.lang import Integer

        return Integer(value)
    except Exception:
        return value


def relative_path_for(subdir=None):
    base = f"Download/{DOWNLOAD_SUBDIR}"
    return f"{base}/{subdir}" if subdir else base


def publish_to_downloads(src_path, filename=None, mime_type=EPUB_MIME, subdir=None):
    src_path = Path(src_path)
    filename = filename or src_path.name
    relative_path = relative_path_for(subdir)

    try:
        from android.content import ContentValues
        from android.provider import MediaStore
    except Exception as e:  # not on Android at all
        raise RuntimeError(f"MediaStore unavailable: {e}") from e

    if (sdk_int() or 0) < 29:
        raise RuntimeError("MediaStore.Downloads requires Android 10 (API 29)")

    values = ContentValues()
    values.put(MediaStore.MediaColumns.DISPLAY_NAME, filename)
    values.put(MediaStore.MediaColumns.MIME_TYPE, mime_type)
    values.put(MediaStore.MediaColumns.RELATIVE_PATH, relative_path)
    # IS_PENDING hides the file from other apps until the copy is finished.
    values.put(MediaStore.MediaColumns.IS_PENDING, _java_int(1))

    resolver = _context().getContentResolver()
    uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
    if uri is None:
        raise RuntimeError(f"MediaStore refused to create {relative_path}/{filename}")

    try:
        stream = resolver.openOutputStream(uri)
        if stream is None:
            raise RuntimeError("MediaStore returned no output stream")
        try:
            with open(src_path, "rb") as f:
                while chunk := f.read(256 * 1024):
                    stream.write(chunk)
            stream.flush()
        finally:
            stream.close()
    except Exception:
        # Don't leave a half-written pending row behind.
        try:
            resolver.delete(uri, None, None)
        except Exception:
            pass
        raise

    values.clear()
    values.put(MediaStore.MediaColumns.IS_PENDING, _java_int(0))
    resolver.update(uri, values, None, None)

    return relative_path.replace("Download/", "Downloads/", 1) + f"/{filename}"


def _media_columns():
    try:
        from android.provider import MediaStore
    except Exception:
        return None
    if (sdk_int() or 0) < 29:
        return None
    return MediaStore


def find_in_downloads(filename, subdir=None):
    media_store = _media_columns()
    if media_store is None:
        return None

    try:
        from android.content import ContentUris

        columns = media_store.MediaColumns
        selection = f"{columns.RELATIVE_PATH}=? AND {columns.DISPLAY_NAME}=?"
        # MediaStore stores RELATIVE_PATH with a trailing separator.
        args = [relative_path_for(subdir) + "/", filename]

        cursor = _context().getContentResolver().query(
            media_store.Downloads.EXTERNAL_CONTENT_URI,
            [columns._ID],
            selection,
            args,
            None,
        )
        if cursor is None:
            return None
        try:
            if not cursor.moveToFirst():
                return None
            row_id = cursor.getLong(0)
        finally:
            cursor.close()

        return ContentUris.withAppendedId(
            media_store.Downloads.EXTERNAL_CONTENT_URI, row_id
        )
    except Exception as e:
        logger.warning(f"MediaStore lookup failed for {filename}: {e}")
        return None


def read_from_downloads(filename, subdir=None):
    uri = find_in_downloads(filename, subdir)
    if uri is None:
        return None

    try:
        from java import jarray, jbyte
        from java.io import ByteArrayOutputStream

        stream = _context().getContentResolver().openInputStream(uri)
        if stream is None:
            return None
        try:
            buffer = jarray(jbyte)(_COPY_BUFFER)
            sink = ByteArrayOutputStream()
            while True:
                count = stream.read(buffer)
                if count <= 0:
                    break
                sink.write(buffer, 0, count)
            return bytes(sink.toByteArray())
        finally:
            stream.close()
    except Exception as e:
        logger.warning(f"Could not read {filename} from Downloads: {e}")
        return None


def delete_from_downloads(filename, subdir=None):
    uri = find_in_downloads(filename, subdir)
    if uri is None:
        return False
    try:
        return _context().getContentResolver().delete(uri, None, None) > 0
    except Exception as e:
        logger.warning(f"Could not delete {filename}: {e}")
        return False


def write_bytes_to_downloads(data, filename, mime_type, subdir=None):
    with tempfile.NamedTemporaryFile(suffix=f"-{filename}", delete=False) as handle:
        handle.write(data)
        staged = Path(handle.name)
    try:
        delete_from_downloads(filename, subdir)
        return publish_to_downloads(staged, filename, mime_type, subdir)
    finally:
        staged.unlink(missing_ok=True)


def diagnostics():
    rows = []

    rows.append(("On Android", "yes" if is_android() else "no"))

    sdk = sdk_int()
    rows.append(("API level", str(sdk) if sdk is not None else "UNREADABLE"))
    rows.append(
        ("All files access", "granted" if has_all_files_access() else "not granted")
    )
    rows.append(
        ("Can request it", "yes" if can_request_all_files_access() else "no")
    )

    downloads = public_downloads_dir()
    rows.append(("Downloads dir", str(downloads) if downloads else "UNREADABLE"))

    if downloads:
        folder = downloads / DOWNLOAD_SUBDIR
        rows.append(("Our folder", str(folder)))
        try:
            exists = folder.is_dir()
        except OSError as e:
            rows.append(("Folder readable", f"no ({e})"))
        else:
            rows.append(("Folder exists", "yes" if exists else "no"))
            if exists:
                try:
                    names = sorted(p.name for p in folder.iterdir())
                    rows.append(("Entries", str(len(names))))
                    rows.append(
                        ("Contents", ", ".join(names[:12]) or "(empty)")
                    )
                except OSError as e:
                    rows.append(("Folder listing", f"denied ({e})"))

    rows.append(
        ("Direct write", "yes" if ensure_public_folder() else "no (MediaStore)")
    )
    return rows


def diagnostics_text():
    return "\n".join(f"{label}: {value}" for label, value in diagnostics())
