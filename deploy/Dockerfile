# Data API builder, with this repo's configuration baked in.
#
# The image is pinned to an exact version on purpose. "latest" would mean a
# redeploy months from now could silently pick up different behaviour, which is
# the opposite of what you want from an audited data path.
#
# To move to a newer version, change the tag here, rebuild, and redeploy. Check
# what is available:
#   https://mcr.microsoft.com/v2/azure-databases/data-api-builder/tags/list
FROM mcr.microsoft.com/azure-databases/data-api-builder:2.0.12

# The container reads its configuration from /App/dab-config.json.
#
# Note what is NOT copied: there is no connection string, no secret and no
# credential in this image. The connection string arrives at runtime as an
# environment variable, and it names a managed identity rather than a password.
# The image is therefore safe to store in a registry that others can pull from.
COPY dab-config.json /App/dab-config.json

# Data API builder listens on 5000 inside the container. Azure Container Apps
# maps external 443 to this port.
EXPOSE 5000
