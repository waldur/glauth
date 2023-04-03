#!/bin/sh

set -e

mkdir -p /app/config

echo "";
echo "Starting script refreshing glauth config";
echo "";

/app/docker/refresh-glauth-config.sh &> /var/log/refresh-glauth-config.log &

timeout_counter=0
until [ -f /app/config/config.cfg ] || [ $timeout_counter -eq 12 ]
do
  echo "Waiting for /app/config/config.cfg..."
  sleep 5
  timeout_counter=$((timeout_counter + 1))
done

if [ ! -f /app/config/config.cfg ]; then
  echo "The config file doesn't exist. Exiting"
  exit 1
fi

echo "";
echo "Version and build information:";
echo "";

# Output version string to logs
/app/glauth --version


echo "";
echo "Starting GLauth now.";
echo "";

# Run app
/app/glauth -c /app/config/config.cfg

echo ""
echo "GLauth has exited."
echo "Exiting."
