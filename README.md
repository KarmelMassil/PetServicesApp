# Pet Services App
This repository contains a Kubernetes deployment of a multi-service Pet Store application, developed for the Cloud Computing home assignment (2025-26). It deploys the pet-store and pet-order services from Assignments #1 and #2 as a load-balanced, persistent, namespaced system behind an NGINX reverse proxy.
## Features
* **Multi-Instance Pet Store:** Two independent `pet-store` instances (`pet-store1`, `pet-store2`), each backed by its own MongoDB collection, implementing the same REST API as Assignments #1 and #2.
* **Load-Balanced Pet Order Service:** Two replicas of `pet-order` behind a single Kubernetes Service, with atomic purchase-ID allocation in MongoDB to prevent double-purchases.
* **NGINX Reverse Proxy:** Single entry point to the cluster; routes requests by path, restricts allowed HTTP methods per endpoint, and enforces owner-only access via the `OwnerPC` header.
* **Persistent Storage:** MongoDB backed by a `PersistentVolume`/`PersistentVolumeClaim` pair, ensuring data survives Pod restarts.
* **Secret-Managed API Key:** The pet-store's Ninja API key is injected via a Kubernetes `Secret` rather than hardcoded.
* **Namespaced Deployment:** All resources are isolated in a dedicated `pet-app` namespace.
## Files Included
* `namespace.yaml`
* `pet-store/` — `deployment1.yaml`, `deployment2.yaml`, `service1.yaml`, `service2.yaml`, `ninja-api-secret.yaml`, `Dockerfile`, `app/`
* `pet-order/` — `deployment.yaml`, `service.yaml`, `Dockerfile`, `app/`
* `database/` — `deployment.yaml`, `service.yaml`, `persistentVolume.yaml`, `persistentVolumeClaim.yaml`
* `nginx/` — `deployment.yaml`, `service.yaml`, `configmap.yaml`
## Prerequisites
* Docker (for building and running containers)
* kind v0.30.0 (for the local Kubernetes cluster — later versions have a known bug)
* kubectl (for deploying and managing resources)
## Cluster Setup
1. Create a local Kubernetes cluster:
   ```bash
   kind create cluster
   ```
2. Create the dedicated namespace:
   ```bash
   kubectl apply -f namespace.yaml
   ```
3. Add your Ninja API key to `pet-store/ninja-api-secret.yaml` (base64-encoded, or use `stringData` for plain text).
## Kubernetes Deployment
To build and deploy the full system:
1. Build the Docker images (run from the repository root, since the Dockerfiles expect that build context):
   ```bash
   docker build -t pet-store-image:latest -f pet-store/Dockerfile .
   docker build -t pet-order-image:latest -f pet-order/Dockerfile .
   ```
2. Load the images into the cluster:
   ```bash
   kind load docker-image pet-store-image:latest
   kind load docker-image pet-order-image:latest
   ```
3. Deploy all resources:
   ```bash
   kubectl apply -f database/
   kubectl apply -f pet-store/
   kubectl apply -f pet-order/
   kubectl apply -f nginx/
   ```
## Testing
To verify the deployment, check that all Pods are running:
```bash
kubectl get pods -n pet-app
```
Then exercise the API through NGINX (exposed on `NodePort 31322`):
```bash
curl http://localhost:31322/pet-types1
curl http://localhost:31322/pet-types2
curl http://localhost:31322/transactions
```
To confirm persistence, delete the MongoDB pod and verify your data is still there once it restarts:
```bash
kubectl delete pod <mongodb-pod-name> -n pet-app
curl http://localhost:31322/transactions
```