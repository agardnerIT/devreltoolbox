from utils import *

# This runs every time a user attaches or re-attaches to the environment
# It is required due to the way docker networking and IPs work
# Without this, `kubectl get nodes` would fail
# So this logic ensure that the kubeconfig file correctly points to the kind cluster
#
# This is triggered automatically via the on_attach hook in the devcontainer.json file
configureClusterConnection()