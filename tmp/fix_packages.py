import pathlib
p = pathlib.Path("src/pypackhub/ui/views/packages.py")
text = p.read_text(encoding="utf-8")
marker = "    def _change_selected(self, kind: ActionKind) -> None:\n        \"\"\"Run one action for the current selection (or all outdated packages).\"\"\""
first = text.find(marker)
if first == -1:
    raise SystemExit("could not find _change_selected")
second = text.find(marker, first + len(marker))
if second == -1:
    raise SystemExit("only one _change_selected found — no duplication")
before = text[:first]
after = text[second:]
clean = (
    marker
    + "\n"
    + "        rows = self.selected_rows()\n"
    + "\n"
    + "        if kind is ActionKind.UPGRADE_ALL:\n"
    + "            if self._inventory is None or not self._inventory.outdated_count:\n"
    + "                self.status_changed.emit(self._shell.text(TranslationKey.PACKAGES_NO_SELECTION))\n"
    + "                return\n"
    + "            self.confirm_and_run(\n"
    + "                self.service.action_for_upgrade_all(self._inventory, user=self._may_use_user_flag())\n"
    + "            )\n"
    + "            return\n"
    + "\n"
    + "        if len(rows) != 1:\n"
    + "            self._need_single_selection()\n"
    + "            return\n"
    + "\n"
    + "        row = rows[0]\n"
    + "        user = self._may_use_user_flag()\n"
    + "        if kind is ActionKind.UNINSTALL:\n"
    + "            action = self.service.action_for_uninstall(row.name)\n"
    + "        else:\n"
    + "            action = self.service.action_for_upgrade(row.name, user=user)\n"
    + "        self.confirm_and_run(action)\n"
)
p.write_text(before + clean + after, encoding="utf-8")
print("rewrote packages.py; new length", len(before + clean + after))
