from utils import *

# IMPORTANT NOTE!
# Most of the capabilities of this container are actually defined as "features" in .devcontainer/devcontainer.json
# It's only those things that DO NOT have a "feature" that we need to "manually" install here!
# It is ALWAYS best to use a devcontainer.json feature over this custom logic
# You can find features here:
# - https://containers.dev/features
#
# If YOU are a codespace author, and what you're about to implement IS NOT a one-off
# it's probably best to wrap YOUR custom logic in a devcontainer feature
# so that others can much more easily re-use your functionality!
# See here for a devcontainer feature template: https://github.com/devcontainers/feature-starter