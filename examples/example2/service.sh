#!/bin/bash

echo "Service is starting..."
sleep 5
echo "Service is ready"
tail -f /dev/null # Keep the service running
