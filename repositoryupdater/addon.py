"""
Add-on Module.

Represents / handles all Home Assistant add-on specific logic
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from io import SEEK_CUR, SEEK_END, TextIOWrapper
from pathlib import PurePosixPath
from shutil import copyfile, copytree, rmtree

import click
import crayons
import emoji
import semver
import yaml
from git import Repo
from github.Commit import Commit
from github.GithubException import GithubException, UnknownObjectException
from github.GitRelease import GitRelease
from github.Repository import Repository
from jinja2 import BaseLoader, Environment

from repositoryupdater.github import GitHub

from .const import CHANNEL_STABLE, CHANNEL_BETA, CHANNEL_EDGE


class Addon:
    """Object representing an Home Assistant add-on."""

    repository_target: str
    addon_target: str
    image: str
    repository: Repo
    updating: bool
    addon_repository: Repository
    current_version: str
    current_commit: Commit
    current_release: GitRelease
    existing_config_filename: str | None = None
    latest_version: str
    latest_release: GitRelease
    latest_is_release: bool
    latest_commit: Commit
    up_to_date: bool
    archs: list
    name: str
    description: str
    slug: str
    url: str
    channel: str
    github: GitHub
    git_repo: Repo

    def __init__(
        self,
        github: GitHub,
        repository: Repo,
        repository_target: str,
        image: str,
        addon_repository: Repository,
        addon_target: str,
        channel: str,
        updating: bool,
    ):
        """Initialize a new Home Assistant add-on object."""
        self.github = github
        self.repository_target = repository_target
        self.addon_target = addon_target
        self.image = image
        self.repository = repository
        self.addon_repository = addon_repository
        self.archs = ["aarch64", "amd64", "armhf", "armv7", "i386"]
        self.latest_is_release = True
        self.updating = updating
        self.channel = channel
        self.current_version = None
        self.latest_release = None
        self.latest_commit = None
        self.up_to_date = True

        click.echo(
            "Loading add-on information from: %s" % self.addon_repository.html_url
        )

        self.__load_current_info()
        if self.updating:
            self.__load_latest_info(channel)
            if self.needs_update(False):
                self.up_to_date = False
                click.echo(
                    crayons.yellow("This add-on has an update waiting to be published!")
                )
            else:
                click.echo(crayons.green("This add-on is up to date."))

    def clone_repository(self):
        """Clone the add-on source to a local working directory."""
        click.echo("Cloning add-on git repository...", nl=False)
        self.git_repo = self.github.clone(
            self.addon_repository, tempfile.mkdtemp(prefix=self.addon_target)
        )
        self.git_repo.git.checkout(self.current_commit.sha)
        click.echo(crayons.green("Cloned!"))

    def update(self):
        """Update this add-on inside the given add-on repository."""
        if not self.updating:
            click.echo(
                crayons.red("Cannot update add-on that was marked not being updated")
            )
            sys.exit(1)

        self.current_version = self.latest_version
        self.current_release = self.latest_release if self.latest_is_release else None
        self.current_commit = self.latest_commit

        self.clone_repository()
        self.ensure_addon_dir()
        self.generate_addon_config()
        self.update_static_files()
        self.generate_readme()
        self.generate_addon_changelog()

    def __load_current_info(self):
        """Load current add-on version information and current config."""
        config_files = ("config.json", "config.yaml", "config.yml")
        for config_file in config_files:
            if os.path.exists(
                os.path.join(
                    self.repository.working_dir, self.repository_target, config_file
                )
            ):
                self.existing_config_filename = config_file
                break

        if self.existing_config_filename is None:
            click.echo("Current version: %s" % crayons.yellow("Not available"))
            return False

        with open(
            os.path.join(
                self.repository.working_dir,
                self.repository_target,
                self.existing_config_filename,
            ),
            "r",
            encoding="utf8",
        ) as f:
            current_config = (
                json.load(f)
                if self.existing_config_filename.endswith(".json")
                else yaml.safe_load(f)
            )

        self.current_version = current_config["version"]
        self.name = current_config["name"]
        self.description = current_config["description"]
        self.slug = current_config["slug"]
        self.url = current_config["url"]
        if "arch" in current_config:
            self.archs = current_config["arch"]

        current_parsed_version = False
        try:
            current_parsed_version = semver.parse(self.current_version)
        except ValueError:
            pass

        if current_parsed_version:
            try:
                ref = self.addon_repository.get_git_ref("tags/v" + self.current_version)
            except UnknownObjectException:
                ref = self.addon_repository.get_git_ref("tags/" + self.current_version)
            self.current_commit = self.addon_repository.get_commit(ref.object.sha)
        else:
            try:
                self.current_commit = self.addon_repository.get_commit(
                    "v" + self.current_version
                )
            except GithubException:
                self.current_commit = self.addon_repository.get_commit(
                    self.current_version
                )

        click.echo(
            "Current version: %s (%s)"
            % (crayons.magenta(self.current_version), self.current_commit.sha[:7])
        )

    def __load_latest_info(self, channel: str):
        """Determine latest available add-on version and config."""
        for release in self.addon_repository.get_releases():
            self.latest_version = release.tag_name.lstrip("v")
            def _is_prerelease(version: str) -> bool:
                try:
                    return semver.parse_version_info(version).prerelease is not None
                except ValueError:
                    return "-" in version
            prerelease = (
                release.prerelease
                or _is_prerelease(self.latest_version)
            )

            if release.draft or (prerelease and channel != CHANNEL_BETA):
                continue
            self.latest_release = release
            break

        if self.latest_release:
            ref = self.addon_repository.get_git_ref(
                "tags/" + self.latest_release.tag_name
            )
            if ref.object.type == "tag":
                ref = self.addon_repository.get_git_tag(
                    ref.object.sha
                )
            self.latest_commit = self.addon_repository.get_commit(ref.object.sha)

        if channel == CHANNEL_EDGE:
            last_commit = self.addon_repository.get_commits()[0]
            if not self.latest_commit or last_commit.sha != self.latest_commit.sha:
                self.latest_version = last_commit.sha[:7]
                self.latest_commit = last_commit
                self.latest_is_release = False

        config_files = ["config.json", "config.yaml", "config.yml"]
        # Ensure existing filename is at the start of the list
        if self.existing_config_filename is not None:
            config_files.insert(
                0, config_files.pop(config_files.index(self.existing_config_filename))
            )

        latest_config_file = None
        config_file = None
        for config_file in config_files:
            try:
                latest_config_file = self.addon_repository.get_contents(
                    str(PurePosixPath(self.addon_target, config_file)), self.latest_commit.sha
                )
                break
            except UnknownObjectException:
                pass

        if config_file is None or latest_config_file is None:
            click.echo(
                crayons.red(
                    "An error occurred while loading the remote add-on "
                    "configuration file"
                )
            )
            sys.exit(1)

        latest_config = (
            json.loads(latest_config_file.decoded_content)
            if config_file.endswith(".json")
            else yaml.safe_load(latest_config_file.decoded_content)
        )

        self.name = latest_config["name"]
        self.description = latest_config["description"]
        self.slug = latest_config["slug"]
        self.url = latest_config["url"]
        if "arch" in latest_config:
            self.archs = latest_config["arch"]

        click.echo(
            "Latest version: %s (%s)"
            % (crayons.magenta(self.latest_version), self.latest_commit.sha[:7])
        )

    def needs_update(self, force: bool):
        """Determine whether or not there is add-on updates available."""
        return self.updating and (
            force
            or self.current_version != self.latest_version
            or self.current_commit != self.latest_commit
        )

    def ensure_addon_dir(self):
        """Ensure the add-on target directory exists."""
        addon_path = os.path.join(self.repository.working_dir, self.repository_target)
        addon_translations_path = os.path.join(addon_path, "translations")

        if not os.path.exists(addon_path):
            os.mkdir(addon_path)

        if not os.path.exists(addon_translations_path):
            os.mkdir(addon_translations_path)

    def generate_addon_config(self):
        """Generate add-on configuration file."""
        click.echo("Generating add-on configuration...", nl=False)

        config_files = ("config.json", "config.yaml", "config.yml")
        config_file = None
        for config_file in config_files:
            if os.path.exists(
                os.path.join(self.git_repo.working_dir, self.addon_target, config_file)
            ):
                break
            config_file = None

        if config_file is None:
            click.echo(crayons.red("Failed!"))
            sys.exit(1)

        with open(
            os.path.join(self.git_repo.working_dir, self.addon_target, config_file),
            encoding="utf8",
        ) as f:
            config = (
                json.load(f) if config_file.endswith(".json") else yaml.safe_load(f)
            )

        config["version"] = self.current_version
        config["image"] = self.image

        for old_config_file in config_files:
            try:
                os.unlink(
                    os.path.join(
                        self.repository.working_dir,
                        self.repository_target,
                        old_config_file,
                    )
                )
            except:
                pass

        with open(
            os.path.join(
                self.repository.working_dir, self.repository_target, config_file
            ),
            "w",
            encoding="utf8",
        ) as outfile:
            if config_file.endswith(".json"):
                json.dump(
                    config,
                    outfile,
                    ensure_ascii=False,
                    indent=2,
                    separators=(",", ": "),
                )
            else:
                yaml.dump(config, outfile, default_flow_style=False, sort_keys=False)

        click.echo(crayons.green("Done"))

    CHANGELOG_HEADER = "# Changelog\n\n"
    CHANGELOG_MARKER = "generated by the repository updater action"

    def generate_addon_changelog(self):
        """Re-generate the add-on changelog."""
        click.echo("Re-generating add-on CHANGELOG.md file...", nl=False)

        changelog_path = os.path.join(
            self.repository.working_dir, self.repository_target, "CHANGELOG.md"
        )

        def __published_at_formatted(release: GitRelease):
            return release.published_at.strftime("%Y-%m-%d")

        def __message_first_line(commit: Commit):
            return commit.commit.message.split("\n", maxsplit=1)[0]

        def __changelog_updater(args: list[str]):
            command = ["changelog-updater", "update",
                f"--path-to-changelog={changelog_path}",
                "--parse-github-usernames",
                "--no-interaction",
                "--write",
                "--quiet",
            ]
            command.extend(args)
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if result.returncode != 0:
                click.echo(crayons.red("Failed!"))
                click.echo(crayons.red(f"changelog-updater returned non-zero exit code: {result.returncode}"))
                click.echo(result.stdout.decode())
                sys.exit(1)

        def __update_changelog(release: GitRelease):
            __changelog_updater([
                f"--latest-version={release.tag_name.lstrip("v")}",
                f"--release-date={__published_at_formatted(release)}",
                f"--release-notes={emoji.emojize(release.body, language="alias")}",
            ])

        def __write_changelog(changelog: str, append: bool = False):
            with open(
                changelog_path,
                "w" if not append else "a",
                encoding="utf8",
            ) as outfile:
                outfile.write(changelog)

        def __read_last_nonempty_line(file: TextIOWrapper, encoding: str):
            try:
                file.seek(-1, SEEK_END)
                while file.read(1) == b"\n":
                    file.seek(-2, SEEK_CUR)
                file.seek(-2, SEEK_CUR)
                while file.read(1) != b"\n":
                    file.seek(-2, SEEK_CUR)
            except OSError:
                file.seek(0)
            return file.read().decode(encoding).rstrip()

        def __read_last_nonempty_changelog_line():
            with open(changelog_path, "rb") as changelog_file:
                return __read_last_nonempty_line(changelog_file, encoding="utf8")


        if self.latest_is_release and self.channel == CHANNEL_STABLE:
            # On the stable channel in case of a new stable release,
            # add a new entry to the existing changelog
            # but don't accept changelog not generated by this tool
            changelog_exists = os.path.exists(changelog_path)
            # If this is a legacy changelog, remove it and start fresh
            if changelog_exists and Addon.CHANGELOG_MARKER not in __read_last_nonempty_changelog_line():
                os.remove(changelog_path)
                changelog_exists = False
            if self.up_to_date and changelog_exists:
                click.echo(crayons.blue("Skipping"))
                return
            else:
                if not changelog_exists:
                    __write_changelog(Addon.CHANGELOG_HEADER)
                __update_changelog(self.current_release)
                # The changelog-updater's AST removes comments (like SU's HTML renderer will),
                # so we need to add them back again
                __write_changelog(
                    "\n"
                    "[//]: # (do not remove these and the surrounding blank lines)\n"
                    "[//]: # (" + Addon.CHANGELOG_MARKER + ")\n"
                    "\n",
                    append=True)
        elif self.latest_is_release:
            # On the beta or edge channel in case of a new stable release,
            # or on the beta channel in case of a new prerelease,
            # copy the release notes as-is to the changelog
            # Note: there is no history like on the stable channel
            __write_changelog(Addon.CHANGELOG_HEADER)
            __update_changelog(self.current_release)
        else:
            changelog = ""
            if self.latest_release:
                # On the edge channel in case of a new commit (merged PR),
                # collect the commit messages since the latest release
                changelog = Addon.CHANGELOG_HEADER
                changelog += f"## Unreleased changes since {self.latest_release.tag_name.lstrip("v")} - {__published_at_formatted(self.latest_release)}\n\n"
                compare = self.addon_repository.compare(
                    self.latest_release.tag_name, self.current_commit.sha
                )
                for commit in reversed(compare.commits):
                    changelog += f"- {__message_first_line(commit)}\n"
            else:
                # On the edge channel in case of a new commit (merged PR),
                # when there is no latest release (initial commits or transferred repo without releases),
                # collect the commit messages since the latest version bump or from the beginning (max 100 commits)
                last_version_bump_commit_and_diff = self.git_repo.git.log(
                    "--date=format-local:%Y-%m-%d",
                    "--format=tformat:%H %ad",
                    "-p",
                    "-m",
                    "-U0",
                    "-G", "^version:",
                    "--first-parent",
                    "--max-count=1",
                    "--follow", self.slug + "/config.*"
                ).splitlines()
                if len(last_version_bump_commit_and_diff) == 0:
                    # initial commits, no version bump found
                    stop_commit_sha = None
                    changelog = Addon.CHANGELOG_HEADER
                    changelog += "## Unreleased changes\n\n"
                else:
                    # found last version bump commit, probably transferred repo without releases
                    stop_commit_sha, stop_commit_date = last_version_bump_commit_and_diff[0].split(" ", maxsplit=1)
                    for diff_line in last_version_bump_commit_and_diff[1:]:
                        if diff_line.startswith("+version:"):
                            stop_commit_version = diff_line.split(":", maxsplit=1)[1].strip()
                            break
                    changelog = Addon.CHANGELOG_HEADER
                    changelog += f"## Unreleased changes since {stop_commit_version} - {stop_commit_date}\n\n"
                commits = self.git_repo.git.log(
                    "--format=tformat:%H %s",
                    "--first-parent",
                ).splitlines()
                counter = 0
                for commit in commits:
                    commit_sha, commit_message = commit.split(" ", maxsplit=1)
                    if stop_commit_sha and commit_sha == stop_commit_sha:
                        break
                    if counter >= 100:
                        changelog += f"\nNote: More than {counter} commits, further commits are suppressed.\n"
                        break
                    counter += 1
                    changelog += f"- {commit_message}\n"
            changelog = emoji.emojize(changelog, language="alias")
            __write_changelog(changelog)

        click.echo(crayons.green("Done"))

    def update_static_files(self):
        """Update the static add-on files within the repository."""
        self.update_static("logo.png")
        self.update_static("icon.png")
        self.update_static("README.md")
        self.update_static("DOCS.md")
        self.update_static("apparmor.txt")
        self.update_static("translations")
        self.update_static("transfer.yaml")

    def update_static(self, file):
        """Download latest static file/directory from add-on repository."""
        click.echo(f"Syncing add-on static {file}...", nl=False)
        local_file = os.path.join(
            self.repository.working_dir, self.repository_target, file
        )
        remote_file = os.path.join(self.git_repo.working_dir, self.addon_target, file)

        if os.path.exists(remote_file) and os.path.isfile(remote_file):
            copyfile(remote_file, local_file)
            click.echo(crayons.green("Done"))
        elif os.path.exists(remote_file) and os.path.isdir(remote_file):
            rmtree(local_file)
            copytree(remote_file, local_file)
            click.echo(crayons.green("Done"))
        elif os.path.isfile(local_file):
            os.remove(local_file)
            click.echo(crayons.yellow("Removed"))
        else:
            click.echo(crayons.blue("Skipping"))

    def generate_readme(self):
        """Re-generate the add-on readme based on a template."""
        click.echo("Re-generating add-on README.md file...", nl=False)

        addon_file = os.path.join(
            self.git_repo.working_dir, self.addon_target, ".README.j2"
        )
        if not os.path.exists(addon_file):
            click.echo(crayons.blue("Skipping"))
            return

        local_file = os.path.join(
            self.repository.working_dir, self.repository_target, "README.md"
        )

        data = self.get_template_data()

        jinja = Environment(
            loader=BaseLoader(),
            trim_blocks=True,
            extensions=["jinja2.ext.loopcontrols"],
        )

        with open(local_file, "w", encoding="utf8") as outfile:
            outfile.write(
                jinja.from_string(open(addon_file, encoding="utf8").read()).render(
                    **data
                )
            )

        click.echo(crayons.green("Done"))

    def get_template_data(self):
        """Return a dictionary with add-on information."""
        data = {}
        if not self.current_version:
            return data

        data["name"] = self.name
        data["channel"] = self.channel
        data["description"] = self.description
        data["url"] = self.url
        data["repo"] = self.addon_repository.html_url
        data["repo_slug"] = self.addon_repository.full_name
        data["archs"] = self.archs
        data["slug"] = self.slug
        data["target"] = self.repository_target
        data["image"] = self.image
        data["images"] = {}
        for arch in self.archs:
            data["images"][arch] = self.image.replace("{arch}", arch)

        try:
            semver.parse(self.current_version)
            data["version"] = "v" + self.current_version
        except ValueError:
            data["version"] = "v" + self.current_version if "." in self.current_version else self.current_version

        data["commit"] = self.current_commit.sha

        try:
            data["date"] = self.current_release.created_at
        except AttributeError:
            data["date"] = self.current_commit.last_modified

        return data

    def cleanup(self):
        """Cleanup after you leave."""
        if hasattr(self, 'git_repo'):
            shutil.rmtree(self.git_repo.working_dir, True)
