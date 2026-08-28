"""TextClient and Universal Tracker commands for the in-game AP chat.

The real Archipelago TextClient separates local ``/commands`` from server-side
``!commands``.  This processor keeps that same model while adapting operations
which would normally control a standalone client to the embedded Toontown
client and its existing tracker page.
"""

import re
from collections import Counter
from difflib import get_close_matches

from BaseClasses import ItemClassification, MultiWorld
from apworld.toontown import (ITEM_DEFINITIONS, ITEM_NAME_TO_ID, get_item_groups,
                             get_location_groups, test_location)
from apworld.toontown import locations
from apworld.toontown.items import get_item_def_from_id
from apworld.toontown.ruledefs import passes_rule


class InGameArchipelagoCommandProcessor:
    """Process TextClient/UT slash commands against the live local toon."""

    TEXT_COMMANDS = {
        "help": "Show commands, or detailed help for one command.",
        "license": "Show Archipelago client license information.",
        "connect": "Connect to a MultiWorld server: /connect [address]",
        "disconnect": "Disconnect from the MultiWorld server.",
        "exit": "Disconnect the embedded client (the game remains open).",
        "received": "List received items, optionally filtered by text.",
        "missing": "List generated checks not completed yet, optionally filtered.",
        "items": "List all Toontown item names, optionally filtered.",
        "locations": "List generated location names, optionally filtered.",
        "item_groups": "List item groups, or members of one named group.",
        "location_groups": "List location groups, or members of one named group.",
        "ready": "Toggle the AP ready status for this slot.",
    }

    TRACKER_COMMANDS = {
        "inventory": "List the tracker's current item inventory.",
        "prog_inventory": "List progression items in the tracker inventory.",
        "locations_in_logic": "List unchecked locations currently in logic.",
        "event_inventory": "Describe event state represented by the embedded tracker.",
        "event_locations": "Describe event locations represented by the embedded tracker.",
        "manually_collect": "Add one item to tracker simulation only.",
        "reset_manually_collect": "Clear all manually simulated tracker items.",
        "ignore": "Hide a location from the embedded tracker.",
        "ignore_all": "Hide all locations currently in logic.",
        "unignore": "Return a hidden location to the embedded tracker.",
        "list_ignored": "List locations hidden from the embedded tracker.",
        "reset_ignored": "Return all hidden locations to the embedded tracker.",
        "toggle_auto_tab": "Report the live in-game tracker mode.",
        "get_logical_path": "Explain reachability for a location.",
        "explain": "Explain the live rules for a location.",
        "explain_more": "Repeat the last explanation with individual rule results.",
        "faris_asked": "Print useful embedded Universal Tracker diagnostics.",
    }

    ALIASES = {
        "commands": "help",
        "logic": "locations_in_logic",
        "path": "get_logical_path",
        "unignore_all": "reset_ignored",
    }

    def __init__(self, panel, avatar):
        self.panel = panel
        self.avatar = avatar
        self.last_explained_location = ""

    @property
    def commands(self):
        commands = dict(self.TEXT_COMMANDS)
        commands.update(self.TRACKER_COMMANDS)
        return commands

    def process(self, raw):
        """Return True when *raw* was a slash command handled locally."""
        raw = str(raw).strip()
        if not raw.startswith("/"):
            return False

        command_line = raw[1:].strip()
        if not command_line:
            self._cmd_help("")
            return True

        command, _, argument = command_line.partition(" ")
        command = self.ALIASES.get(command.lower().replace("-", "_"),
                                   command.lower().replace("-", "_"))
        method = getattr(self, "_cmd_%s" % command, None)
        if method is None:
            suggestion = get_close_matches(command, self.commands, n=1, cutoff=0.55)
            suffix = " Did you mean /%s?" % suggestion[0] if suggestion else ""
            self.output("Unknown command /%s.%s Use /help for the command list."
                        % (command, suffix), error=True)
            return True

        self.panel.addCommandEcho(raw)
        try:
            method(argument.strip())
        except Exception as error:
            self.output("/%s could not complete: %s" % (command, error), error=True)
        return True

    def output(self, text, error=False):
        self.panel.addCommandOutput(str(text), error=error)

    def output_list(self, title, entries, empty="No matching entries found."):
        entries = [str(entry) for entry in entries]
        if not entries:
            self.output(empty)
            return
        self.output("%s (%s)" % (title, len(entries)))
        for start in range(0, len(entries), 12):
            self.output("\n".join(entries[start:start + 12]))

    @staticmethod
    def _matches(value, filter_text):
        return not filter_text or filter_text.casefold() in str(value).casefold()

    def _generated_location_ids(self):
        """Flat list of generated location IDs from slot data.

        ``local_locations`` is stored as a list of ``[unique_id, name]`` pairs, so
        each entry is unwrapped to just its ID here.
        """
        ids = []
        for entry in self.avatar.slotData.get("local_locations", []):
            if isinstance(entry, (list, tuple)) and entry:
                ids.append(entry[0])
            else:
                ids.append(entry)
        return ids

    def _generated_location_names(self):
        return [locations.LOCATION_ID_TO_NAME[location_id]
                for location_id in self._generated_location_ids()
                if location_id in locations.LOCATION_ID_TO_NAME]

    def _tracker_manual_items(self):
        if not hasattr(self.avatar, "apTrackerManualItems"):
            self.avatar.apTrackerManualItems = Counter()
        return self.avatar.apTrackerManualItems

    def _tracker_ignored_locations(self):
        if not hasattr(self.avatar, "apTrackerIgnoredLocations"):
            self.avatar.apTrackerIgnoredLocations = set()
        return self.avatar.apTrackerIgnoredLocations

    def _refresh_tracker(self):
        page = getattr(self.avatar, "locationPage", None)
        if page and hasattr(page, "refreshFromArchipelagoCommand"):
            page.refreshFromArchipelagoCommand()

    def _logic_state(self):
        page = getattr(self.avatar, "locationPage", None)
        if page and hasattr(page, "getTrackerLogicState"):
            return page.getTrackerLogicState()
        return self.avatar

    def _in_logic_definitions(self, include_ignored=False):
        generated = set(self._generated_location_ids())
        checked = set(self.avatar.getCheckedLocations())
        ignored = set() if include_ignored else self._tracker_ignored_locations()
        state = self._logic_state()
        result = []
        for definition in locations.LOCATION_DEFINITIONS:
            location_id = locations.LOCATION_NAME_TO_ID.get(definition.name.value)
            if location_id not in generated or location_id in checked or location_id in ignored:
                continue
            if test_location(definition, state, MultiWorld, 1, self.avatar.slotData):
                result.append(definition)
        return result

    def _resolve_name(self, argument, candidates, kind):
        argument = argument.strip()
        if not argument:
            self.output("Please provide a %s name." % kind, error=True)
            return None
        by_fold = {name.casefold(): name for name in candidates}
        if argument.casefold() in by_fold:
            return by_fold[argument.casefold()]
        contains = [name for name in candidates if argument.casefold() in name.casefold()]
        if len(contains) == 1:
            return contains[0]
        if len(contains) > 1:
            self.output_list("Multiple %s matches; be more specific" % kind, contains[:24])
            return None
        close = get_close_matches(argument, list(candidates), n=3, cutoff=0.45)
        if close:
            self.output("Unknown %s. Close matches: %s" % (kind, ", ".join(close)), error=True)
        else:
            self.output("Unknown %s: %s" % (kind, argument), error=True)
        return None

    # Archipelago TextClient commands.
    def _cmd_help(self, argument):
        name = self.ALIASES.get(argument.lower().lstrip("/").replace("-", "_"),
                                argument.lower().lstrip("/").replace("-", "_"))
        if name:
            description = self.commands.get(name)
            if description:
                self.output("/%s - %s" % (name, description))
            else:
                self.output("No help is available for /%s." % name, error=True)
            return
        text_names = "  ".join("/%s" % name for name in self.TEXT_COMMANDS)
        tracker_names = "  ".join("/%s" % name for name in self.TRACKER_COMMANDS)
        self.output("ARCHIPELAGO TEXT CLIENT\n%s" % text_names)
        self.output("UNIVERSAL TRACKER\n%s" % tracker_names)
        self.output("Server commands such as !hint, !release, !collect, and !players "
                    "are sent directly to Archipelago. Use /help <command> for details.")

    def _cmd_license(self, _argument):
        self.output("Archipelago is licensed under GPLv3.\n"
                    "https://github.com/ArchipelagoMW/Archipelago")

    def _cmd_connect(self, argument):
        self.avatar.sendArchipelagoChat("!connect%s" % ((" " + argument) if argument else ""))

    def _cmd_disconnect(self, _argument):
        self.avatar.sendArchipelagoChat("!disconnect")

    def _cmd_exit(self, _argument):
        self.output("The embedded client will disconnect; Toontown will remain open.")
        self.avatar.sendArchipelagoChat("!disconnect")

    def _cmd_received(self, argument):
        received = []
        for index, item_id in self.avatar.getReceivedItems():
            definition = get_item_def_from_id(item_id)
            name = definition.name.value if definition else "Unknown Item [%s]" % item_id
            if self._matches(name, argument):
                received.append("%s. %s" % (index + 1, name))
        self.output_list("Received items, sorted by time", received,
                         "No received items matched that filter.")

    def _cmd_missing(self, argument):
        checked = set(self.avatar.getCheckedLocations())
        missing = [name for name in self._generated_location_names()
                   if locations.LOCATION_NAME_TO_ID.get(name) not in checked
                   and self._matches(name, argument)]
        self.output_list("Missing location checks", missing,
                         "No missing location checks found.")

    def _cmd_items(self, argument):
        names = [definition.name.value for definition in ITEM_DEFINITIONS
                 if self._matches(definition.name.value, argument)]
        self.output_list("Toontown item names", names)

    def _cmd_locations(self, argument):
        names = [name for name in self._generated_location_names()
                 if self._matches(name, argument)]
        self.output_list("Generated Toontown locations", names)

    def _output_group(self, argument, groups, kind):
        if not argument:
            self.output_list("%s groups" % kind, sorted(groups))
            return
        key = self._resolve_name(argument, groups, "%s group" % kind.lower())
        if key:
            self.output_list("%ss in %s" % (kind, key), groups[key])

    def _cmd_item_groups(self, argument):
        self._output_group(argument, get_item_groups(), "Item")

    def _cmd_location_groups(self, argument):
        self._output_group(argument, get_location_groups(), "Location")

    def _cmd_ready(self, _argument):
        self.avatar.sendArchipelagoChat("!ready")

    # Universal Tracker commands adapted to the embedded tracker.
    def _inventory_entries(self, progression_only, filter_text):
        counts = Counter(item_id for _, item_id in self.avatar.getReceivedItems())
        counts.update(self._tracker_manual_items())
        entries = []
        for item_id, count in counts.items():
            definition = get_item_def_from_id(item_id)
            if not definition:
                continue
            if progression_only and not definition.classification & ItemClassification.progression:
                continue
            if self._matches(definition.name.value, filter_text):
                manual = self._tracker_manual_items().get(item_id, 0)
                suffix = " (%s simulated)" % manual if manual else ""
                entries.append("%sx: %s%s" % (count, definition.name.value, suffix))
        return sorted(entries, key=str.casefold)

    def _cmd_inventory(self, argument):
        self.output_list("Current tracker inventory",
                         self._inventory_entries(False, argument))

    def _cmd_prog_inventory(self, argument):
        self.output_list("Current progression inventory",
                         self._inventory_entries(True, argument))

    def _cmd_locations_in_logic(self, argument):
        names = [definition.name.value for definition in self._in_logic_definitions()
                 if self._matches(definition.name.value, argument)]
        self.output_list("Locations currently in logic", names,
                         "No matching unchecked locations are currently in logic.")

    def _cmd_event_inventory(self, _argument):
        self.output("Toontown's embedded tracker evaluates live Toon state directly, so UT's "
                    "internal event items are folded into /inventory and are not duplicated here.")

    def _cmd_event_locations(self, _argument):
        self.output("Toontown's embedded tracker folds event reachability into the normal live "
                    "location rules. Use /locations_in_logic or /explain <location>.")

    def _cmd_manually_collect(self, argument):
        item_name = self._resolve_name(argument, ITEM_NAME_TO_ID, "item")
        if not item_name:
            return
        item_id = ITEM_NAME_TO_ID[item_name]
        self._tracker_manual_items()[item_id] += 1
        self._refresh_tracker()
        self.output("Added %s to tracker simulation only." % item_name)

    def _cmd_reset_manually_collect(self, _argument):
        self._tracker_manual_items().clear()
        self._refresh_tracker()
        self.output("Cleared all manually simulated tracker items.")

    def _cmd_ignore(self, argument):
        candidates = self._generated_location_names()
        name = self._resolve_name(argument, candidates, "location")
        if not name:
            return
        self._tracker_ignored_locations().add(locations.LOCATION_NAME_TO_ID[name])
        self._refresh_tracker()
        self.output("Hidden %s from the embedded tracker." % name)

    def _cmd_ignore_all(self, _argument):
        definitions = self._in_logic_definitions(include_ignored=True)
        self._tracker_ignored_locations().update(
            locations.LOCATION_NAME_TO_ID[definition.name.value]
            for definition in definitions)
        self._refresh_tracker()
        self.output("Hidden %s currently in-logic locations." % len(definitions))

    def _cmd_unignore(self, argument):
        ignored = self._tracker_ignored_locations()
        names = [locations.LOCATION_ID_TO_NAME[item] for item in ignored
                 if item in locations.LOCATION_ID_TO_NAME]
        name = self._resolve_name(argument, names, "ignored location")
        if not name:
            return
        ignored.discard(locations.LOCATION_NAME_TO_ID[name])
        self._refresh_tracker()
        self.output("Returned %s to the embedded tracker." % name)

    def _cmd_list_ignored(self, argument):
        names = [locations.LOCATION_ID_TO_NAME[item]
                 for item in self._tracker_ignored_locations()
                 if item in locations.LOCATION_ID_TO_NAME
                 and self._matches(locations.LOCATION_ID_TO_NAME[item], argument)]
        self.output_list("Ignored tracker locations", sorted(names),
                         "No tracker locations are ignored.")

    def _cmd_reset_ignored(self, _argument):
        self._tracker_ignored_locations().clear()
        self._refresh_tracker()
        self.output("Returned all ignored locations to the embedded tracker.")

    def _cmd_toggle_auto_tab(self, _argument):
        self.output("The embedded Toontown tracker is always live and follows the current Toon; "
                    "there is no separate map tab to toggle.")

    @staticmethod
    def _friendly_rule_name(rule):
        name = getattr(rule, "name", str(rule).rsplit(".", 1)[-1])
        return re.sub(r"(?<!^)(?=[A-Z])", " ", name)

    def _explain_location(self, argument, detailed=False):
        generated = self._generated_location_names()
        name = self._resolve_name(argument, generated, "generated location")
        if not name:
            return
        self.last_explained_location = name
        definition = next((entry for entry in locations.LOCATION_DEFINITIONS
                           if entry.name.value == name), None)
        if not definition:
            self.output("No local rule definition exists for %s." % name, error=True)
            return
        state = self._logic_state()
        checked = locations.LOCATION_NAME_TO_ID[name] in set(self.avatar.getCheckedLocations())
        reachable = test_location(definition, state, MultiWorld, 1, self.avatar.slotData)
        ignored = locations.LOCATION_NAME_TO_ID[name] in self._tracker_ignored_locations()
        status = "CHECKED" if checked else ("IN LOGIC" if reachable else "OUT OF LOGIC")
        if ignored:
            status += ", IGNORED"
        joiner = "ANY" if definition.rule_logic_or else "ALL"
        self.output("%s\nStatus: %s | Region: %s | Rule mode: %s"
                    % (name, status, definition.region.value, joiner))
        if not definition.rules:
            self.output("This location has no additional item/state requirements.")
            return
        rule_lines = []
        for rule in definition.rules:
            passed = passes_rule(rule, state, definition, MultiWorld, 1, self.avatar.slotData)
            prefix = "PASS" if passed else "NEED"
            rule_lines.append("[%s] %s%s" % (
                prefix, self._friendly_rule_name(rule),
                " (raw: %s)" % getattr(rule, "name", rule) if detailed else ""))
        self.output("\n".join(rule_lines))

    def _cmd_explain(self, argument):
        self._explain_location(argument)

    def _cmd_get_logical_path(self, argument):
        self._explain_location(argument)

    def _cmd_explain_more(self, argument):
        target = argument or self.last_explained_location
        if not target:
            self.output("Use /explain <location> first.", error=True)
            return
        self._explain_location(target, detailed=True)

    def _cmd_faris_asked(self, _argument):
        manual = sum(self._tracker_manual_items().values())
        ignored = len(self._tracker_ignored_locations())
        in_logic = len(self._in_logic_definitions())
        checked = len(self.avatar.getCheckedLocations())
        generated = len(self.avatar.slotData.get("local_locations", []))
        self.output("Embedded UT diagnostics\nGenerated: %s | Checked: %s | In logic: %s\n"
                    "Simulated items: %s | Ignored locations: %s"
                    % (generated, checked, in_logic, manual, ignored))
