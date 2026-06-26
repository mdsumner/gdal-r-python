# MVP: Running an R `targets` Pipeline on Cloud Hardware via Coiled Run

This Minimum Viable Product (MVP) demonstrates how to orchestrate a parallelized R `targets` pipeline on a high-powered cloud virtual machine using **Coiled Run**, without having to manually manage cloud infrastructure.

Coiled handles provisioning the cloud VM (AWS/GCP), synchronising your local project files up to the machine, running the containerized execution environment, and automatically downloading the compiled `_targets/` data store cache back to your local computer before destroying the VM.

## Prerequisites

1. Install the Coiled CLI tool locally:
   ```bash
   pip install coiled
   ```
2. Log in and authenticate your cloud account:
   ```bash
   coiled login
   ```

---

## File Setup

Create the following two files in an empty local project directory.

### 1. `_targets.R`
This script defines the pipeline infrastructure. It uses the `crew` package to ensure that once the pipeline reaches the multi-core cloud VM, it boots parallel background R workers to speed up execution.

```R
library(targets)
library(crew)

# Configure crew to parallelize locally inside the Coiled VM
tar_option_set(
  controller = crew_controller_local(
    name = "coiled_workers",
    workers = 3 # Utilise 3 parallel background R workers
  )
)

# Dummy pipeline targets to test concurrent execution and syncing
list(
  tar_target(data_chunk_1, { Sys.sleep(5); "Result Alpha" }),
  tar_target(data_chunk_2, { Sys.sleep(5); "Result Beta" }),
  tar_target(combined_report, paste(data_chunk_1, data_chunk_2))
)
```

### 2. `run_pipeline.R`
This helper script is what Coiled invokes inside the Docker container to bootstrap dependencies and execute the pipeline.

```R
# Bootstraps package dependencies if they aren't baked into the image
if (!requireNamespace("targets", quietly = TRUE)) install.packages("targets")
if (!requireNamespace("crew", quietly = TRUE)) install.packages("crew")

# Run the target pipeline
targets::tar_make()

# Visual confirmation output
print(targets::tar_read(combined_report))
```

---

## Execution

Open your terminal inside the project directory and run the following command to spin up a 4-core cloud instance using an official Rocker container:

```bash
coiled run \
  --vm-type m6i.xlarge \
  --container rocker/r-ver:latest \
  Rscript run_pipeline.R
```

### What happens under the hood:
1. **Infrastructure**: Coiled spins up an `m6i.xlarge` instance (4 vCPUs, 16GB RAM) in your cloud provider.
2. **Environment**: It pulls and runs the specified `rocker/r-ver:latest` Docker image.
3. **Upload Sync**: Coiled uploads `_targets.R` and `run_pipeline.R` from your local folder into the container.
4. **Execution**: The cloud machine executes `run_pipeline.R`. Target logs will stream live into your local terminal.
5. **Download Sync & Teardown**: Upon successful execution, Coiled downloads the entire generated `_targets/` data store directory back to your machine and cleanly destroys the cloud instance so you only pay for the exact compute runtime used.
6. 
