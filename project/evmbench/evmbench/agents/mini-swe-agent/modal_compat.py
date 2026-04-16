"""Compatibility helpers for mini-swe-agent Modal runners."""

from __future__ import annotations

import os
from collections.abc import Sequence


DEFAULT_REGISTRY_SETUP_COMMANDS = (
    "RUN python -m pip install --ignore-installed pip wheel",
)


def patch_swerex_modal_image_builder(
    setup_commands: Sequence[str] = DEFAULT_REGISTRY_SETUP_COMMANDS,
) -> None:
    """Patch SWE-ReX registry image creation for the installed Modal SDK.

    SWE-ReX 1.x still calls ``modal.Image.from_registry`` with the old
    ``secrets=`` shape and Docker-style secret keys. Current Modal expects
    ``secret=`` and ``REGISTRY_USERNAME`` / ``REGISTRY_PASSWORD``. The setup
    pre-step also avoids Modal legacy builder failures on Debian-managed
    ``pip`` and ``wheel`` packages in EVMBench images.
    """

    import modal
    import swerex.deployment.modal as swerex_modal

    image_builder = swerex_modal._ImageBuilder
    if getattr(image_builder, "_evmbench_registry_patch_applied", False):
        return

    setup_dockerfile_commands = list(setup_commands)

    def from_registry(self, image: str) -> modal.Image:
        self.logger.info(f"Building image from docker registry {image}")
        kwargs = {"setup_dockerfile_commands": setup_dockerfile_commands}
        username = os.environ.get("DOCKER_USERNAME")
        password = os.environ.get("DOCKER_PASSWORD")
        if username and password:
            secret = modal.Secret.from_dict(
                {
                    "REGISTRY_USERNAME": username,
                    "REGISTRY_PASSWORD": password,
                }
            )
            self.logger.debug("Docker login credentials were provided")
            return modal.Image.from_registry(image, secret=secret, **kwargs)

        self.logger.warning("DOCKER_USERNAME and DOCKER_PASSWORD not set. Using public images.")
        return modal.Image.from_registry(image, **kwargs)

    image_builder.from_registry = from_registry
    image_builder._evmbench_registry_patch_applied = True
