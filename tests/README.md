# Hadoop Cluster Test Scripts

This directory contains .bat scripts for managing and testing the Hadoop cluster.

## 📁 Files

### 🚀 Cluster Management
- **`../start-cluster.bat`** - Start cluster with image rebuild (in root)
- **`../stop-cluster.bat`** - Stop cluster with optional cleanup (in root)

### 🧪 Testing
- **`test-cluster.bat`** - Complete cluster testing
- **`test-hdfs.bat`** - HDFS components testing only
- **`test-yarn.bat`** - YARN components testing only

## 🎯 Usage

### Quick Start
1. Run `../start-cluster.bat` to start the cluster
2. Wait for completion (30 seconds)
3. Run `test-cluster.bat` to verify

### Step-by-step Testing
1. `test-hdfs.bat` - test HDFS operations
2. `test-yarn.bat` - test YARN and MapReduce
3. `test-cluster.bat` - comprehensive testing

### Shutdown
- `../stop-cluster.bat` - stop with data cleanup option

## 🌐 Web Interfaces

Available after cluster startup:

| Service | URL | Description |
|---------|-----|-------------|
| HDFS NameNode | http://localhost:9870 | HDFS Management |
| YARN ResourceManager | http://localhost:8088 | Resource Management |
| HDFS DataNode | http://localhost:9864 | DataNode Status |
| YARN NodeManager | http://localhost:8042 | NodeManager Status |

## 📊 What's Tested

### HDFS Tests:
- ✅ Container status
- ✅ NameNode and DataNode processes
- ✅ HDFS cluster status
- ✅ Web interface availability
- ✅ File create/read/write operations
- ✅ HDFS blocks verification

### YARN Tests:
- ✅ ResourceManager and NodeManager status
- ✅ YARN nodes list
- ✅ Applications list
- ✅ Web interface availability
- ✅ MapReduce job execution (WordCount)
- ✅ Resources and queues verification

### General Tests:
- ✅ Network connectivity between containers
- ✅ HDFS and YARN integration
- ✅ Log verification
- ✅ Comprehensive operation testing

## ⚠️ Requirements

- Docker Desktop for Windows
- Docker Compose
- curl (usually built into Windows 10+)

## 🔧 Troubleshooting

### If cluster doesn't start:
1. Check that Docker Desktop is running
2. Ensure ports 9870, 8088, 9864, 8042 are free
3. Run `../stop-cluster.bat` with cleanup and try again

### If tests fail:
1. Wait for full service startup (30+ seconds)
2. Check logs: `docker-compose logs`
3. Restart the cluster

## 📝 Logs

Container logs can be viewed with:
```bash
docker-compose logs namenode
docker-compose logs datanode
```
