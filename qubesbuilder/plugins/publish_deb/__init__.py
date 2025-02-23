# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2021 Frédéric Pierret (fepitre) <frederic@invisiblethingslab.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program. If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later
from datetime import datetime

from qubesbuilder.component import QubesComponent
from qubesbuilder.config import Config
from qubesbuilder.distribution import QubesDistribution
from qubesbuilder.executors import ExecutorError
from qubesbuilder.pluginmanager import PluginManager
from qubesbuilder.log import get_logger
from qubesbuilder.plugins import DEBDistributionPlugin
from qubesbuilder.plugins.publish import PublishPlugin, PublishError

log = get_logger("publish_deb")


class DEBPublishPlugin(DEBDistributionPlugin, PublishPlugin):
    """
    DEBPublishPlugin manages Debian distribution publication.

    Stages:
        - publish - Create repository to be published and uploaded to remote mirror.

    Entry points:
        - build
    """

    stages = ["publish"]
    dependencies = ["publish"]

    def __init__(
        self,
        component: QubesComponent,
        dist: QubesDistribution,
        config: Config,
        manager: PluginManager,
        **kwargs,
    ):
        super().__init__(component=component, dist=dist, config=config, manager=manager)

    @classmethod
    def get_debian_suite_from_repository_publish(cls, dist, repository_publish):
        # set debian suite according to publish repository
        debian_suite = dist.name
        if repository_publish == "current-testing":
            debian_suite += "-testing"
        elif repository_publish == "security-testing":
            debian_suite += "-securitytesting"
        elif repository_publish == "unstable":
            debian_suite += "-unstable"
        return debian_suite

    def sign_metadata(self, executor, target_dir, debian_suite, sign_key):
        """Sign repository metadata

        Do it manually, as reprepro does not support alternative gpg client"""

        for (opt, out_name) in (
            ("--detach-sign", "Release.gpg"),
            ("--clearsign", "InRelease"),
        ):
            try:
                release_dir = target_dir / "dists" / debian_suite
                cmd = [
                    f"{self.config.gpg_client} {opt} --armor --local-user {sign_key} "
                    f"--batch --no-tty --output {release_dir / out_name} {release_dir / 'Release'}"
                ]
                log.info(
                    f"{self.component}:{self.dist}:{debian_suite}: Signing metadata ({out_name})."
                )
                executor.run(cmd)
            except ExecutorError as e:
                msg = f"{self.component}:{self.dist}:{debian_suite}: Failed to sign metadata ({out_name})."
                raise PublishError(msg) from e

    def publish(self, executor, directory, keyring_dir, repository_publish, sign_key):
        # directory basename will be used as prefix for some artifacts
        directory_bn = directory.mangle()

        # Build artifacts (source included)
        build_artifacts_dir = self.get_dist_component_artifacts_dir(stage="build")

        # Publish repository
        artifacts_dir = self.get_repository_publish_dir() / self.dist.type

        # Read information from build stage
        build_info = self.get_dist_artifacts_info(stage="build", basename=directory_bn)

        if not build_info.get("changes", None):
            log.info(f"{self.component}:{self.dist}:{directory}: Nothing to publish.")
            return

        log.info(f"{self.component}:{self.dist}:{directory}: Publishing packages.")

        # Verify signatures (sanity check, refuse to publish if packages weren't signed)
        try:
            log.info(f"{self.component}:{self.dist}:{directory}: Verifying signatures.")
            cmd = []
            for file in ("dsc", "changes", "buildinfo"):
                fname = build_artifacts_dir / build_info[file]
                cmd += [f"gpg2 -q --homedir {keyring_dir} --verify {fname}"]
            executor.run(cmd)
        except ExecutorError as e:
            msg = (
                f"{self.component}:{self.dist}:{directory}: Failed to check signatures."
            )
            raise PublishError(msg) from e

        # Publishing packages
        try:
            changes_file = build_artifacts_dir / build_info["changes"]
            target_dir = (
                artifacts_dir / f"{self.config.qubes_release}/{self.dist.package_set}"
            )

            # reprepro options to ignore surprising binary and arch
            reprepro_options = f"--ignore=surprisingbinary --ignore=surprisingarch --keepunreferencedfiles -b {target_dir}"

            debian_suite = self.get_debian_suite_from_repository_publish(
                self.dist, repository_publish
            )

            # reprepro command
            cmd = [f"reprepro {reprepro_options} include {debian_suite} {changes_file}"]
            executor.run(cmd)
        except ExecutorError as e:
            msg = (
                f"{self.component}:{self.dist}:{directory}: Failed to publish packages."
            )
            raise PublishError(msg) from e

        self.sign_metadata(
            executor=executor,
            target_dir=target_dir,
            debian_suite=debian_suite,
            sign_key=sign_key,
        )

    def unpublish(
        self,
        executor,
        directory,
        repository_publish,
        sign_key,
    ):
        # Publish repository
        artifacts_dir = self.get_repository_publish_dir() / self.dist.type

        # directory basename will be used as prefix for some artifacts
        directory_bn = directory.mangle()

        # Read information from build stage
        build_info = self.get_dist_artifacts_info(stage="build", basename=directory_bn)

        if not build_info.get("changes", None):
            log.info(f"{self.component}:{self.dist}:{directory}: Nothing to publish.")
            return

        log.info(f"{self.component}:{self.dist}:{directory}: Unpublishing packages.")

        # Unpublishing packages
        try:
            target_dir = (
                artifacts_dir / f"{self.config.qubes_release}/{self.dist.package_set}"
            )

            # reprepro options to ignore surprising binary and arch
            reprepro_options = (
                f"--ignore=surprisingbinary --ignore=surprisingarch -b {target_dir}"
            )

            # set debian suite according to publish repository
            debian_suite = self.dist.name
            if repository_publish == "current-testing":
                debian_suite += "-testing"
            elif repository_publish == "security-testing":
                debian_suite += "-securitytesting"
            elif repository_publish == "unstable":
                debian_suite += "-unstable"

            # reprepro command
            source_name, source_version = build_info["package-release-name-full"].split(
                "_", 1
            )
            cmd = [
                f"reprepro {reprepro_options} removesrc {debian_suite} {source_name} {source_version}"
            ]
            executor.run(cmd)
        except ExecutorError as e:
            msg = f"{self.component}:{self.dist}:{directory}: Failed to unpublish packages."
            raise PublishError(msg) from e

        self.sign_metadata(
            executor=executor,
            target_dir=target_dir,
            debian_suite=debian_suite,
            sign_key=sign_key,
        )

    def run(
        self,
        stage: str,
        repository_publish: str = None,
        ignore_min_age: bool = False,
        unpublish: bool = False,
        **kwargs,
    ):
        """
        Run plugin for given stage.
        """
        # Run stage defined by parent class
        super().run(stage=stage)

        if stage != "publish" or not self.has_component_packages("publish"):
            return

        executor = self.config.get_executor_from_config(stage)
        parameters = self.get_parameters(stage)

        # Check if we have a signing key provided
        sign_key = self.config.sign_key.get(
            self.dist.distribution, None
        ) or self.config.sign_key.get("deb", None)
        if not sign_key:
            log.info(f"{self.component}:{self.dist}: No signing key found.")
            return

        # Check if we have a gpg client provided
        if not self.config.gpg_client:
            log.info(f"{self.component}: Please specify GPG client to use!")
            return

        # Sign artifacts
        sign_artifacts_dir = self.get_dist_component_artifacts_dir(stage="sign")

        # Keyring used for signing
        keyring_dir = sign_artifacts_dir / "keyring"

        repository_publish = repository_publish or self.config.repository_publish.get(
            "components"
        )
        if not repository_publish:
            raise PublishError("Cannot determine repository for publish")

        if not unpublish:

            # repository-publish directory
            artifacts_dir = self.get_repository_publish_dir() / self.dist.type

            if not sign_artifacts_dir.exists():
                raise PublishError("Cannot find keyring from sign stage.")

            # Create publish repository skeleton
            create_skeleton_cmd = [
                f"{self.manager.entities['publish_deb'].directory}/scripts/create-skeleton",
                self.config.qubes_release,
                self.dist.fullname,
                str(artifacts_dir),
            ]
            cmd = [" ".join(create_skeleton_cmd)]
            try:
                executor.run(cmd)
            except ExecutorError as e:
                msg = f"{self.component}:{self.dist}: Failed to create repository skeleton."
                raise PublishError(msg) from e
            # Check if publish repository is valid
            self.validate_repository_publish(repository_publish)

            # Check if we already published packages into the provided repository
            if all(
                self.is_published(
                    basename=directory.mangle(),
                    repository=repository_publish,
                )
                for directory in parameters["build"]
            ):
                log.info(
                    f"{self.component}:{self.dist}: Already published to '{repository_publish}'."
                )
                return

            # Check if we can publish into current
            if repository_publish == "current" and not all(
                self.can_be_published_in_stable(
                    basename=directory.mangle(),
                    ignore_min_age=ignore_min_age,
                )
                for directory in parameters["build"]
            ):
                failure_msg = (
                    f"{self.component}:{self.dist}: "
                    f"Refusing to publish to 'current' as packages are not "
                    f"uploaded to 'current-testing' or 'security-testing' "
                    f"for at least {self.config.min_age_days} days."
                )
                raise PublishError(failure_msg)

            for directory in parameters["build"]:
                # directory basename will be used as prefix for some artifacts
                directory_bn = directory.mangle()

                build_info = self.get_dist_artifacts_info(
                    stage="build", basename=directory_bn
                )
                publish_info = self.get_dist_artifacts_info(
                    stage=stage, basename=directory_bn
                )

                if not build_info:
                    raise PublishError(
                        f"{self.component}:{self.dist}:{directory}: Cannot find build info."
                    )

                # If previous publication to a repo has been done and does not correspond to current
                # build artifacts, we delete previous publications. It happens if we modify local
                # sources and then publish into repository with the same version and release.
                info = build_info
                if publish_info:
                    if build_info["source-hash"] != publish_info["source-hash"]:
                        log.info(
                            f"{self.component}:{self.dist}:{directory}: Current build hash does not match previous one."
                        )
                        for repository in publish_info.get("repository-publish", []):
                            self.unpublish(
                                executor=executor,
                                directory=directory,
                                repository_publish=repository,
                                sign_key=sign_key,
                            )
                    else:
                        info = publish_info

                self.publish(
                    executor=executor,
                    directory=directory,
                    keyring_dir=keyring_dir,
                    repository_publish=repository_publish,
                    sign_key=sign_key,
                )

                # Save package information we published for committing into current
                info.setdefault("repository-publish", []).append(
                    {
                        "name": repository_publish,
                        "timestamp": datetime.utcnow().strftime("%Y%m%d%H%M"),
                    }
                )
                self.save_dist_artifacts_info(
                    stage="publish", basename=directory_bn, info=info
                )

        if unpublish:
            if not all(
                self.is_published(
                    basename=directory.mangle(),
                    repository=repository_publish,
                )
                for directory in parameters["build"]
            ):
                log.info(
                    f"{self.component}:{self.dist}: Not published to '{repository_publish}'."
                )
                return

            for directory in parameters["build"]:
                # directory basename will be used as prefix for some artifacts
                directory_bn = directory.mangle()

                publish_info = self.get_dist_artifacts_info(
                    stage=stage, basename=directory_bn
                )

                self.unpublish(
                    executor=executor,
                    directory=directory,
                    repository_publish=repository_publish,
                    sign_key=sign_key,
                )

                # Save package information we published for committing into current. If the packages
                # are not published into another repository, we delete the publish stage information.
                publish_info["repository-publish"] = [
                    r
                    for r in publish_info.get("repository-publish", [])
                    if r["name"] != repository_publish
                ]
                if publish_info.get("repository-publish", []):
                    self.save_dist_artifacts_info(
                        stage="publish", basename=directory_bn, info=publish_info
                    )
                else:
                    log.info(
                        f"{self.component}:{self.dist}:{directory}: Not published anywhere else, deleting publish info."
                    )
                    self.delete_dist_artifacts_info(
                        stage="publish", basename=directory_bn
                    )

                # We republish previous package version that has been published previously in the
                # same repository. This is because reprepro does not manage multiversions officially.
                for artifacts_dir in self.get_dist_component_artifacts_dir_history(
                    stage=stage
                ):
                    publish_info = self.get_dist_artifacts_info(
                        stage=stage, basename=directory_bn, artifacts_dir=artifacts_dir
                    )
                    if repository_publish in publish_info.get("repository-publish", []):
                        log.info(
                            f"{self.component}:{self.dist}:{directory}: Updating repository."
                        )
                        self.publish(
                            executor=executor,
                            directory=directory,
                            keyring_dir=keyring_dir,
                            repository_publish=repository_publish,
                            sign_key=sign_key,
                        )
                        break


PLUGINS = [DEBPublishPlugin]
