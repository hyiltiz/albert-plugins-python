# -*- coding: utf-8 -*-
# Copyright (c) 2026 Hormet Yiltiz

"""
Search files using Recoll full-text search via recollq CLI.
Queries the Xapian index maintained by Recoll without loading it into memory.
Two modes: filename search (r) and full-text content search (rr).
"""

import shutil
import subprocess
from base64 import b64decode
from pathlib import Path
from time import sleep
from urllib.parse import unquote, urlparse

from albert import *

md_iid = "3.0"
md_version = "1.1"
md_name = "Recoll"
md_description = "Search files using Recoll full-text search"
md_license = "MIT"
md_url = "https://github.com/albertlauncher/python/tree/main/recoll"
md_authors = "@hyiltiz"


_MIME_ICON_FALLBACKS = {
    'application': 'application-x-generic',
    'audio': 'audio-x-generic',
    'image': 'image-x-generic',
    'text': 'text-x-generic',
    'video': 'video-x-generic',
}


def _mime_to_icon_urls(mtype):
    """Convert a MIME type string to a list of xdg icon URL candidates."""
    if not mtype:
        return ["xdg:text-x-generic"]
    primary = f"xdg:{mtype.replace('/', '-')}"
    category = mtype.split('/')[0] if '/' in mtype else ''
    fallback = f"xdg:{_MIME_ICON_FALLBACKS.get(category, 'text-x-generic')}"
    return [primary, fallback]


def _find_recollq():
    """Find recollq executable, checking PATH first then known locations."""
    found = shutil.which('recollq')
    if found:
        return found
    app_path = '/Applications/recoll.app/Contents/MacOS/recollq'
    if Path(app_path).is_file():
        return app_path
    return None


def _url_to_path(url):
    """Convert a file:// URL to a local file path."""
    parsed = urlparse(url)
    if parsed.scheme == 'file':
        return unquote(parsed.path)
    return url


def _run_recollq(recollq, recoll_conf, max_results, query_str, filename_only=False):
    """Run recollq and return (stdout, error_message) tuple."""
    cmd = [recollq]
    if recoll_conf:
        cmd.extend(['-c', recoll_conf])
    if filename_only:
        cmd.append('-f')
    cmd.extend(['-n', str(max_results)])
    cmd.extend(['-F', 'url filename mtype abstract'])
    cmd.append(query_str)

    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=10
    )
    return proc.stdout


def _parse_results(stdout, plugin_id, show_abstract=False):
    """Parse recollq -F output into StandardItem list."""
    items = []
    for line in stdout.splitlines():
        if not line.strip():
            continue

        parts = line.split()
        if len(parts) < 3:
            continue

        try:
            url = b64decode(parts[0]).decode()
            filename = b64decode(parts[1]).decode()
            mtype = b64decode(parts[2]).decode()
            abstract = b64decode(parts[3]).decode().strip() if len(parts) > 3 else ''
        except Exception:
            continue

        filepath = _url_to_path(url)

        if show_abstract and abstract:
            if len(abstract) > 120:
                abstract = abstract[:120] + '...'
            subtext = f"{abstract}\n{filepath}"
        else:
            subtext = filepath

        items.append(StandardItem(
            id=filepath,
            text=filename,
            subtext=subtext,
            iconUrls=_mime_to_icon_urls(mtype),
            actions=[
                Action("open", "Open",
                       lambda p=filepath: openUrl(f"file://{p}")),
                Action("folder", "Open containing folder",
                       lambda p=filepath: openUrl(
                           f"file://{str(Path(p).parent)}")),
                Action("copy_path", "Copy path",
                       lambda p=filepath: setClipboardText(p)),
                Action("terminal", "Open terminal here",
                       lambda p=filepath: runTerminal(
                           f"cd {str(Path(p).parent)!r}; exec $SHELL")),
            ]
        ))
    return items


class RecollContentSearch(TriggerQueryHandler):
    """Full-text content search via Recoll."""

    def __init__(self, plugin):
        TriggerQueryHandler.__init__(self)
        self._plugin = plugin

    def id(self):
        return "recoll_content"

    def name(self):
        return "Recoll Content Search"

    def description(self):
        return "Full-text content search via Recoll/Xapian"

    def defaultTrigger(self):
        return "rr "

    def synopsis(self, query):
        return "<full-text search>"

    def handleTriggerQuery(self, query):
        p = self._plugin
        stripped = query.string.strip()

        if not p._recollq:
            query.add(StandardItem(
                id=self.id(),
                text="recollq not found",
                subtext="Install Recoll or set the recollq path in plugin settings",
                iconUrls=["xdg:dialog-error"],
                actions=[]
            ))
            return

        if len(stripped) < 2:
            query.add(StandardItem(
                id=self.id(),
                text="Type at least 2 characters",
                subtext="Full-text content search via Recoll/Xapian",
                iconUrls=["xdg:preferences-system-search"],
                actions=[]
            ))
            return

        debounce_ticks = p._debounce_ms // 10
        for _ in range(debounce_ticks):
            sleep(0.01)
            if not query.isValid:
                return

        try:
            stdout = _run_recollq(
                p._recollq, p._recoll_conf, p._max_results,
                stripped, filename_only=False
            )
        except FileNotFoundError:
            query.add(StandardItem(
                id=self.id(),
                text="recollq not found",
                subtext=f"Not found at: {p._recollq}",
                iconUrls=["xdg:dialog-error"],
                actions=[]
            ))
            return
        except subprocess.TimeoutExpired:
            query.add(StandardItem(
                id=self.id(),
                text="Search timed out",
                subtext="Query took longer than 10 seconds",
                iconUrls=["xdg:dialog-warning"],
                actions=[]
            ))
            return

        if not query.isValid:
            return

        items = _parse_results(stdout, self.id(), show_abstract=True)

        if items:
            query.add(items)
        else:
            query.add(StandardItem(
                id=self.id(),
                text="No results",
                subtext=f"No matches for: {stripped}",
                iconUrls=["xdg:dialog-information"],
                actions=[]
            ))


class Plugin(PluginInstance, TriggerQueryHandler):

    def __init__(self):
        PluginInstance.__init__(self)
        TriggerQueryHandler.__init__(self)

        self._recollq = self.readConfig('recollq_path', str) or _find_recollq()
        if self._recollq:
            self.writeConfig('recollq_path', self._recollq)

        self._max_results = self.readConfig('max_results', int) or 25
        self._debounce_ms = self.readConfig('debounce_ms', int) or 200
        self._recoll_conf = self.readConfig('recoll_conf', str) or ''

        self._content_handler = RecollContentSearch(self)

    def extensions(self):
        return [self, self._content_handler]

    def defaultTrigger(self):
        return "r "

    def synopsis(self, query):
        return "<filename search>"

    @property
    def recollq_path(self):
        return self._recollq or ''

    @recollq_path.setter
    def recollq_path(self, value):
        self._recollq = value
        self.writeConfig('recollq_path', value)

    @property
    def max_results(self):
        return self._max_results

    @max_results.setter
    def max_results(self, value):
        self._max_results = int(value)
        self.writeConfig('max_results', self._max_results)

    @property
    def debounce_ms(self):
        return self._debounce_ms

    @debounce_ms.setter
    def debounce_ms(self, value):
        self._debounce_ms = int(value)
        self.writeConfig('debounce_ms', self._debounce_ms)

    @property
    def recoll_conf(self):
        return self._recoll_conf

    @recoll_conf.setter
    def recoll_conf(self, value):
        self._recoll_conf = value
        self.writeConfig('recoll_conf', value)

    def configWidget(self):
        return [
            {
                'type': 'lineedit',
                'label': 'recollq path',
                'property': 'recollq_path',
                'widget_properties': {
                    'placeholderText': '/Applications/recoll.app/Contents/MacOS/recollq',
                }
            },
            {
                'type': 'lineedit',
                'label': 'Recoll config dir',
                'property': 'recoll_conf',
                'widget_properties': {
                    'placeholderText': '~/.recoll (leave empty for default)',
                }
            },
            {
                'type': 'spinbox',
                'label': 'Max results',
                'property': 'max_results',
                'widget_properties': {
                    'minimum': 5,
                    'maximum': 200,
                    'value': self._max_results,
                }
            },
            {
                'type': 'spinbox',
                'label': 'Debounce (ms)',
                'property': 'debounce_ms',
                'widget_properties': {
                    'minimum': 0,
                    'maximum': 1000,
                    'value': self._debounce_ms,
                }
            },
        ]

    def handleTriggerQuery(self, query):
        stripped = query.string.strip()

        if not self._recollq:
            query.add(StandardItem(
                id=self.id(),
                text="recollq not found",
                subtext="Install Recoll or set the recollq path in plugin settings",
                iconUrls=["xdg:dialog-error"],
                actions=[]
            ))
            return

        if len(stripped) < 2:
            query.add(StandardItem(
                id=self.id(),
                text="Type at least 2 characters",
                subtext="Filename search via Recoll/Xapian",
                iconUrls=["xdg:preferences-system-search"],
                actions=[]
            ))
            return

        debounce_ticks = self._debounce_ms // 10
        for _ in range(debounce_ticks):
            sleep(0.01)
            if not query.isValid:
                return

        try:
            stdout = _run_recollq(
                self._recollq, self._recoll_conf, self._max_results,
                stripped, filename_only=True
            )
        except FileNotFoundError:
            query.add(StandardItem(
                id=self.id(),
                text="recollq not found",
                subtext=f"Not found at: {self._recollq}",
                iconUrls=["xdg:dialog-error"],
                actions=[]
            ))
            return
        except subprocess.TimeoutExpired:
            query.add(StandardItem(
                id=self.id(),
                text="Search timed out",
                subtext="Query took longer than 10 seconds",
                iconUrls=["xdg:dialog-warning"],
                actions=[]
            ))
            return

        if not query.isValid:
            return

        items = _parse_results(stdout, self.id(), show_abstract=False)

        if items:
            query.add(items)
        else:
            query.add(StandardItem(
                id=self.id(),
                text="No results",
                subtext=f"No matches for: {stripped}",
                iconUrls=["xdg:dialog-information"],
                actions=[]
            ))
