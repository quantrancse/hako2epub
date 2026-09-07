import asyncio
import threading
from pathlib import Path

import toga
from toga.style import Pack
from toga.style.pack import CENTER, COLUMN, HIDDEN, LEFT, ROW, VISIBLE

from . import storage, tracker, updater
from .downloader import Cancelled, LightNovelDownloader
from .library import Library
from .text import epub_filename, shorten

APP_NAME = 'hako2epub'

ACCESS_REQUEST_DELAY = 0.4
ACCESS_POLL_SECONDS = 1.5
ACCESS_POLL_ATTEMPTS = 160  # ~4 minutes

SWITCH_LABEL_LIMIT = 34

STATUS_LIMIT = 42
HEADING_LIMIT = 30

DISABLED_ALPHA = 0.4

MODE_VOLUMES = 'volumes'
MODE_NOVELS = 'novels'
MODE_DELETE_NOVELS = 'delete-novels'
MODE_DELETE_VOLUMES = 'delete-volumes'
MODE_UPDATES = 'updates'


class HakoApp(toga.App):
    def startup(self):
        self._public_dir = None
        self._library = None
        self._info = tracker.empty()
        self._novel = None
        self._switches = []
        self._mode = None
        self._busy = False
        self._access_requested = False
        self._update_available = None
        self._cancel = threading.Event()
        self._saved = []

        self.main_window = toga.MainWindow(title=APP_NAME)
        self._build_ui()
        self.main_window.show()

        self._setup_storage()
        self._run_in_background(self._version_check_worker)


    def _setup_storage(self):
        if storage.is_android():
            self._public_dir = storage.ensure_public_folder()

            if (
                self._public_dir is None
                and (storage.sdk_int() or 0) <= 28
                and not storage.has_legacy_write_permission()
            ):
                storage.request_legacy_write_permission(self._on_storage_permission)
                return

        self._open_library()

    def _needs_file_access(self) -> bool:
        return (
            storage.is_android()
            and storage.can_request_all_files_access()
            and not storage.has_all_files_access()
        )

    def _show_grant_button(self, show, label=None):
        if label:
            self.grant_btn.text = label
        self._enable(self.grant_btn, True)

        present = self.grant_btn in self._main_box.children
        if show and not present:
            # Sits directly above the URL field, just under the status line.
            index = self._main_box.children.index(self.url_input)
            self._main_box.insert(index, self.grant_btn)
        elif not show and present:
            self._main_box.remove(self.grant_btn)

    def _begin_access_request(self):
        if self._access_requested:
            self._show_grant_button(True, "Grant file access")
            self._set_message(
                "File access needed to see past downloads"
            )
            return

        self._access_requested = True
        self._set_message("Requesting file access...")
        self.loop.create_task(self._request_access_task())

    async def _request_access_task(self):
        await asyncio.sleep(ACCESS_REQUEST_DELAY)

        opened, detail = storage.request_all_files_access()
        if not opened:
            self._show_grant_button(True, "Grant file access")
            self._show_diagnostics(self._access_failure_text(detail))
            self._set_message("Cannot open Settings - see below")
            return

        self._set_message("Turn on All files access, then return")

        for _ in range(ACCESS_POLL_ATTEMPTS):
            await asyncio.sleep(ACCESS_POLL_SECONDS)
            if storage.has_all_files_access():
                self._public_dir = storage.ensure_public_folder()
                self._show_grant_button(False)
                self._open_library()
                return

        # Gave up waiting; leave a way back in.
        self._show_grant_button(True, "Grant file access")
        self._set_message("Still no file access - tap to try again")

    def _show_diagnostics(self, text):
        present = self.diag_view in self._main_box.children
        if text:
            self.diag_view.value = text
            if not present:
                index = self._main_box.children.index(self.url_input)
                self._main_box.insert(index, self.diag_view)
        elif present:
            self._main_box.remove(self.diag_view)

    def _access_failure_text(self, detail) -> str:
        return (
            "Settings could not be opened.\n"
            f"Reason: {detail}\n\n"
            "Grant it manually: Settings > Apps > "
            f"{APP_NAME} > Permissions.\n\n"
            + self._diagnostic_text()
        )

    def _diagnostic_text(self) -> str:
        lines = [storage.diagnostics_text()]
        if self._library is not None:
            try:
                found = self._library.stored_epubs()
            except Exception as e:
                lines.append(f"Scan: failed ({e})")
            else:
                lines.append(f"Mode: {'direct' if self._library.direct else 'MediaStore'}")
                lines.append(f"EPUBs found: {len(found)}")
                for folder, path in found[:10]:
                    lines.append(f"  {folder or '(root)'}/{path.name}")
        return "\n".join(lines)

    def _on_grant(self, widget):
        if storage.has_all_files_access():
            self._public_dir = storage.ensure_public_folder()
            self._show_grant_button(False)
            self._open_library()
            return

        # Refresh the panel each time, so a failed attempt shows current state.
        self._show_diagnostics(self._diagnostic_text())

        opened, detail = storage.request_all_files_access()
        if opened:
            self._show_grant_button(True, "I've enabled it - reload")
            self._set_message(
                "Turn it on, come back, then tap reload"
            )
        else:
            self._show_diagnostics(self._access_failure_text(detail))
            self._set_message("Cannot open Settings - see below")

    def _on_storage_permission(self, granted):
        if granted:
            self._public_dir = storage.ensure_public_folder()
        self._open_library()

    def _open_library(self):
        self._library = Library(
            public_dir=self._public_dir,
            private_dir=Path(self.paths.data),
        )
        try:
            self._info = self._library.load_info()
        except Exception as e:
            self._set_message(f"Could not read ln_info.json: {e}")
            return

        recovered = ''
        if not tracker.novels(self._info):
            recovered = self._recover_from_disk()

        has_library = bool(tracker.novels(self._info))
        self._enable(self.check_btn, has_library)
        self._enable(self.delete_btn, has_library)

        if self._needs_file_access():
            self._begin_access_request()
            return

        self._show_grant_button(False)

        self._show_diagnostics('')
        self._set_message(
            f"Downloads/{storage.DOWNLOAD_SUBDIR} - "
            f"{tracker.summarise(self._info)}{recovered}"
        )

    def _recover_from_disk(self) -> str:
        try:
            info, exact, approximate = self._library.rescan()
        except Exception as e:
            return f" (could not scan folder: {e})"

        if not tracker.novels(info):
            return ''

        self._info = info
        try:
            self._library.save_info(self._info)
        except Exception:
            # Recovery is still useful in memory even if it can't be written.
            pass

        note = f" - recovered {exact + approximate} volume(s) from disk"
        if approximate:
            note += f", {approximate} without exact chapter names"
        return note


    def _build_ui(self):
        main_box = toga.Box(style=Pack(direction=COLUMN, flex=1, margin=10, gap=8))
        self._main_box = main_box

        self.progress_bar = toga.ProgressBar(
            style=Pack(margin_top=2, visibility=HIDDEN), value=0, max=100
        )
        main_box.add(self.progress_bar)

        status_row = toga.Box(
            style=Pack(direction=ROW, align_items=CENTER, gap=6, margin_top=2)
        )

        self.status_label = toga.Label(
            "Paste a novel URL and tap Fetch",
            style=Pack(font_size=13, text_align=LEFT, flex=1),
        )
        status_row.add(self.status_label)

        self.spinner = toga.ActivityIndicator()
        status_row.add(self.spinner)
        main_box.add(status_row)

        self.grant_btn = toga.Button(
            "Grant file access",
            on_press=self._on_grant,
            style=Pack(margin_top=4),
        )

        self.diag_view = toga.MultilineTextInput(
            readonly=True,
            style=Pack(margin_top=4, height=190, font_size=11),
        )

        self.url_input = toga.TextInput(
            placeholder="https://ln.hako.vn/truyen/...",
            style=Pack(margin_top=6),
        )
        main_box.add(self.url_input)

        buttons = toga.Box(style=Pack(direction=ROW, gap=6, margin_top=4))
        self.fetch_btn = toga.Button(
            "Fetch", on_press=self._on_fetch, style=Pack(flex=1)
        )
        buttons.add(self.fetch_btn)
        self.check_btn = toga.Button(
            "Check updates", on_press=self._on_check, style=Pack(flex=1)
        )
        self._enable(self.check_btn, False)
        buttons.add(self.check_btn)
        main_box.add(buttons)

        second_row = toga.Box(style=Pack(direction=ROW, gap=6, margin_top=4))
        self.delete_btn = toga.Button(
            "Delete", on_press=self._on_delete, style=Pack(flex=1)
        )
        self._enable(self.delete_btn, False)
        second_row.add(self.delete_btn)
        self.cancel_btn = toga.Button(
            "Cancel", on_press=self._on_cancel, style=Pack(flex=1)
        )
        self._enable(self.cancel_btn, False)
        second_row.add(self.cancel_btn)
        main_box.add(second_row)

        self.list_label = toga.Label(
            "", style=Pack(font_size=15, font_weight="bold", text_align=LEFT)
        )
        main_box.add(self.list_label)

        # Volumes to download, or updates to apply - see MODE_* above.
        self.choice_box = toga.Box(style=Pack(direction=COLUMN, gap=2))
        main_box.add(
            toga.ScrollContainer(
                content=self.choice_box,
                horizontal=False,
                style=Pack(flex=1),
            )
        )

        self.select_all_btn = toga.Button(
            "Select none", on_press=self._on_toggle_all, style=Pack(margin_top=4)
        )
        self._enable(self.select_all_btn, False)
        main_box.add(self.select_all_btn)

        self.action_btn = toga.Button(
            "Download", on_press=self._on_action, style=Pack(margin_top=4)
        )
        self._enable(self.action_btn, False)
        main_box.add(self.action_btn)


        self.main_window.content = main_box

    def _enable(self, widget, enabled):
        enabled = bool(enabled)
        widget.enabled = enabled
        try:
            widget._impl.native.setAlpha(1.0 if enabled else DISABLED_ALPHA)
        except Exception:
            # No native view yet, or a backend without per-view opacity.
            pass

    def _show_choices(self, rows, mode, action_label, heading, selected=True):
        for switch in self._switches:
            self.choice_box.remove(switch)
        self._switches = []

        for label, payload in rows:
            switch = toga.Switch(
                shorten(label, SWITCH_LABEL_LIMIT),
                value=selected,
                style=Pack(font_size=13),
            )
            # Keep the model object with the widget that represents it.
            switch._payload = payload
            self._switches.append(switch)
            self.choice_box.add(switch)

        self._mode = mode if rows else None
        self.action_btn.text = action_label
        self.list_label.text = shorten(heading, HEADING_LIMIT)
        self.select_all_btn.text = "Select none" if selected else "Select all"

        has_rows = bool(self._switches)
        self._enable(self.action_btn, has_rows and not self._busy)
        self._enable(self.select_all_btn, has_rows and not self._busy)

    def _selected(self):
        return [s._payload for s in self._switches if s.value]

    def _set_busy(self, busy):
        self._busy = busy
        has_rows = bool(self._switches)
        has_library = bool(tracker.novels(self._info))
        self._enable(self.fetch_btn, not busy)
        self._enable(self.check_btn, not busy and has_library)
        self._enable(self.delete_btn, not busy and has_library)
        self._enable(self.action_btn, not busy and has_rows)
        self._enable(self.select_all_btn, not busy and has_rows)
        self._enable(self.cancel_btn, busy)
        for switch in self._switches:
            self._enable(switch, not busy)
        self.progress_bar.style.visibility = VISIBLE if busy else HIDDEN

        if busy:
            self.spinner.start()
            self._set_bar_indeterminate()
        else:
            self.spinner.stop()
            self._set_bar_determinate()
            self.progress_bar.value = 0


    def _on_toggle_all(self, widget):
        turn_on = not all(s.value for s in self._switches)
        for switch in self._switches:
            switch.value = turn_on
        self.select_all_btn.text = "Select none" if turn_on else "Select all"

    def _on_fetch(self, widget):
        url = (self.url_input.value or "").strip()
        if not url:
            self._set_message("Please enter a novel URL")
            return

        self._start("Fetching novel...")
        self._run_in_background(self._fetch_worker, url)

    def _on_check(self, widget):
        entries = [
            entry
            for entry in tracker.novels(self._info)
            if entry.get('ln_url')
        ]
        if not entries:
            self._set_message("Nothing tracked - download one first")
            return

        rows = [
            (
                f"{entry.get('ln_name') or entry['ln_url']}"
                f" - {len(entry.get('vol_list', []))} volume(s)",
                entry,
            )
            for entry in entries
        ]
        self._show_choices(
            rows, MODE_NOVELS, "Check selected", f"{len(rows)} tracked novel(s)"
        )
        self._set_message("Pick novels to check")

    def _on_delete(self, widget):
        rows = [
            (
                f"{entry.get('ln_name') or entry['ln_url']}"
                f" - {len(entry.get('vol_list', []))} volume(s)",
                entry,
            )
            for entry in tracker.novels(self._info)
            if entry.get('ln_url')
        ]
        if not rows:
            self._set_message("Nothing to delete")
            return

        self._show_choices(
            rows,
            MODE_DELETE_NOVELS,
            "Choose volumes",
            f"{len(rows)} novel(s)",
            selected=False,
        )
        self._set_message("Pick novels, then their volumes")

    def _show_delete_volumes(self, entries):
        multiple = len(entries) > 1
        rows = []
        for entry in entries:
            ln_url = entry.get('ln_url')
            novel_name = entry.get('ln_name') or ln_url or '?'
            for volume in entry.get('vol_list', []):
                vol_name = volume.get('vol_name')
                if not ln_url or not vol_name:
                    continue
                label = f'{novel_name} - {vol_name}' if multiple else vol_name
                rows.append((label, (ln_url, novel_name, vol_name)))

        if not rows:
            self._set_message("No volumes recorded")
            return

        heading = (
            f"{len(rows)} volume(s) in {len(entries)} novel(s)"
            if multiple
            else (entries[0].get('ln_name') or 'Volumes')
        )
        self._show_choices(
            rows, MODE_DELETE_VOLUMES, "Delete selected", heading, selected=False
        )
        self._set_message(
            "All volumes of a novel = delete the novel"
        )

    def _novels_fully_selected(self, targets):
        chosen = {}
        for ln_url, _, vol_name in targets:
            chosen.setdefault(ln_url, set()).add(vol_name)

        complete = set()
        for ln_url, vol_names in chosen.items():
            entry = tracker.find_novel(self._info, ln_url)
            if not entry:
                continue
            tracked = {
                volume.get('vol_name') for volume in entry.get('vol_list', [])
            }
            if tracked and tracked <= vol_names:
                complete.add(ln_url)
        return complete

    async def _ask_confirm(self, title, message) -> bool:
        return bool(
            await self.main_window.dialog(toga.ConfirmDialog(title, message))
        )

    async def _confirm_delete(self, targets):
        novels = {url for url, _, _ in targets}
        complete = self._novels_fully_selected(targets)

        detail = f"Delete {len(targets)} volume(s) from {len(novels)} novel(s)?"
        if complete:
            detail += (
                f"\n\n{len(complete)} novel(s) will be removed entirely, "
                "including their folder."
            )
        detail += "\n\nThe EPUB files will be removed and this cannot be undone."

        confirmed = await self._ask_confirm("Delete", detail)
        if not confirmed:
            self._set_message("Delete cancelled")
            return

        self._start(f"Deleting {len(targets)} volume(s)...")
        self._run_in_background(self._delete_worker, targets)

    def _on_action(self, widget):
        chosen = self._selected()
        if not chosen:
            self._set_message("Select at least one item")
            return

        if self._mode == MODE_DELETE_NOVELS:
            self._show_delete_volumes(chosen)
            return

        if self._mode == MODE_DELETE_VOLUMES:
            self.loop.create_task(self._confirm_delete(chosen))
            return

        if self._mode == MODE_NOVELS:
            urls = [entry['ln_url'] for entry in chosen]
            self._start(f"Checking {len(urls)} novel(s) for new chapters...")
            self._run_in_background(self._check_worker, urls)
        elif self._mode == MODE_UPDATES:
            self._start(f"Updating {len(chosen)} volume(s)...")
            self._run_in_background(self._apply_updates_worker, chosen)
        else:
            self._start(f"Preparing {len(chosen)} volume(s)...")
            self._run_in_background(self._download_worker, self._novel, chosen)

    def _on_cancel(self, widget):
        self._cancel.set()
        self._enable(self.cancel_btn, False)
        self._set_message("Stopping after the current chapter...")

    def _start(self, message):
        self._cancel.clear()
        self._saved = []
        self._set_busy(True)
        self._set_message(message)

    def _run_in_background(self, target, *args):
        threading.Thread(target=target, args=args, daemon=True).start()


    def _on_ui_thread(self, fn, *args):
        self.loop.call_soon_threadsafe(fn, *args)

    def _downloader(self):
        return LightNovelDownloader(
            output_dir=str(self._library.staging_dir()),
            library=self._library,
        )

    def _progress_callbacks(self):
        return dict(
            progress=lambda done, total: self._on_ui_thread(
                self._update_progress, done, total
            ),
            status=lambda message: self._on_ui_thread(self._set_message, message),
            on_volume=self._record,
            should_cancel=self._cancel.is_set,
        )

    def _version_check_worker(self):
        newer = updater.newer_version(self.version)
        if newer:
            self._on_ui_thread(self._show_update_available, newer)

    def _fetch_worker(self, url):
        try:
            novel = self._downloader().fetch_novel(url)
        except Exception as e:
            self._on_ui_thread(self._operation_failed, str(e))
        else:
            self._on_ui_thread(self._fetch_complete, novel)

    def _check_worker(self, urls):
        try:
            downloader = self._downloader()
            candidates = []
            for url in urls:
                if self._cancel.is_set():
                    break
                novel = downloader.fetch_novel(url)
                candidates.extend(
                    downloader.find_updates(
                        novel,
                        self._info,
                        status=lambda m: self._on_ui_thread(self._set_message, m),
                        should_cancel=self._cancel.is_set,
                    )
                )
        except Cancelled:
            self._on_ui_thread(self._download_stopped)
        except Exception as e:
            self._on_ui_thread(self._operation_failed, str(e))
        else:
            self._on_ui_thread(self._check_complete, candidates, len(urls))

    def _download_worker(self, novel, volumes):
        try:
            self._downloader().download_volumes(
                novel, volumes, **self._progress_callbacks()
            )
        except Cancelled:
            self._on_ui_thread(self._download_stopped)
        except Exception as e:
            self._on_ui_thread(self._operation_failed, str(e))
        else:
            self._on_ui_thread(self._download_complete)

    def _apply_updates_worker(self, candidates):
        try:
            self._downloader().apply_updates(
                candidates, **self._progress_callbacks()
            )
        except Cancelled:
            self._on_ui_thread(self._download_stopped)
        except Exception as e:
            self._on_ui_thread(self._operation_failed, str(e))
        else:
            self._on_ui_thread(self._update_complete)

    def _delete_worker(self, targets):
        deleted = 0
        missing = 0
        info = self._info
        touched = set()

        for ln_url, novel_name, vol_name in targets:
            filename = epub_filename(vol_name, novel_name)
            if self._library.delete_epub(novel_name, filename):
                deleted += 1
            else:
                missing += 1
            info = tracker.remove_volume(info, ln_url, vol_name)
            touched.add(novel_name)

        for novel_name in touched:
            self._library.prune_novel_folder(novel_name)

        self._info = info
        try:
            self._library.save_info(info)
        except Exception as e:
            self._on_ui_thread(
                self._set_message, f"Deleted, but ln_info.json failed: {e}"
            )
            return

        self._on_ui_thread(self._delete_complete, deleted, missing)

    def _delete_complete(self, deleted, missing):
        self._show_choices([], None, "Download", "")
        self._set_busy(False)
        note = f"Deleted {deleted} volume(s)"
        if missing:
            note += f", {missing} already gone"
        self._set_message(f"{note} - {tracker.summarise(self._info)}")

    def _record(self, result):
        if result.novel is not None:
            self._info = tracker.record_volume(
                self._info, result.novel, result.volume, result.chapter_names
            )
            try:
                self._library.save_info(self._info)
            except Exception as e:
                # Losing the tracker is not worth losing the download over.
                self._on_ui_thread(
                    self._set_message, f"Saved, but ln_info.json failed: {e}"
                )

        self._saved.append(result.location)
        verb = "Updated" if result.appended else "Saved"
        note = f"{verb} {shorten(Path(result.location).name, 30)}"
        if result.skipped_chapters or result.skipped_images:
            note += (
                f" ({result.skipped_chapters} chapter(s), "
                f"{result.skipped_images} image(s) skipped)"
            )
        self._on_ui_thread(self._set_message, note)


    def _set_message(self, message):
        self.status_label.text = shorten(message, STATUS_LIMIT)

    def _show_update_available(self, version):
        self._update_available = version
        # Never talk over a running download; the notice is only informational.
        if not self._busy:
            self._set_message(f"Update v{version} available on GitHub")

    def _set_bar_indeterminate(self):
        self.progress_bar.max = None
        self.progress_bar.start()

    def _set_bar_determinate(self):
        self.progress_bar.stop()
        self.progress_bar.max = 100

    def _update_progress(self, done, total):
        if not total:
            return
        if not self.progress_bar.is_determinate:
            # First countable step: swap from "working" to "how far".
            self._set_bar_determinate()
        self.progress_bar.value = (done / total) * 100

    def _fetch_complete(self, novel):
        self._novel = novel
        self._show_choices(
            [(volume.name, volume) for volume in novel.volumes],
            MODE_VOLUMES,
            "Download",
            novel.name,
        )
        self._set_busy(False)

        tracked = tracker.find_novel(self._info, novel.url)
        suffix = " - tracked, so Check updates finds new chapters" if tracked else ""
        self._set_message(f"{novel.num_volumes} volume(s){suffix}")

    def _check_complete(self, candidates, checked):
        if not candidates:
            self._set_busy(False)
            self._set_message(f"Already up to date ({checked} novel(s) checked)")
            return

        multiple = len({candidate.novel.url for candidate in candidates}) > 1
        rows = [
            (
                f"{candidate.novel.name} - {candidate.label}"
                if multiple
                else candidate.label,
                candidate,
            )
            for candidate in candidates
        ]
        heading = (
            f"{len(candidates)} volume(s) with new chapters"
            if multiple
            else candidates[0].novel.name
        )
        self._show_choices(rows, MODE_UPDATES, "Update selected", heading)
        self._set_busy(False)

        new_chapters = sum(candidate.count for candidate in candidates)
        self._set_message(
            f"{new_chapters} new in {len(candidates)} volume(s)"
        )

    def _download_complete(self):
        self._set_busy(False)
        if len(self._saved) == 1:
            self._set_message(f"Saved to {self._saved[0]}")
        else:
            self._set_message(
                f"Saved {len(self._saved)} EPUBs to "
                f"Downloads/{storage.DOWNLOAD_SUBDIR}"
            )

    def _update_complete(self):
        self._set_busy(False)
        self._set_message(f"Updated {len(self._saved)} volume(s)")

    def _download_stopped(self):
        self._set_busy(False)
        if self._saved:
            self._set_message(
                f"Stopped - kept {len(self._saved)} finished EPUB(s)"
            )
        else:
            self._set_message("Stopped before anything finished")

    def _operation_failed(self, message):
        self._set_busy(False)
        self._set_message(f"Error: {message}")
