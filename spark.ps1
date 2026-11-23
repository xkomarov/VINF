#   .\spark.ps1 spark-submit --master "local[*]" --driver-memory 4g --py-files wiki_regex.py,spark_common.py join.py out/extracted out/wiki out/joined
$image = "f1-spark"
docker image inspect $image *> $null
if ($LASTEXITCODE -ne 0) { docker build -t $image -f "$PSScriptRoot\docker\spark.Dockerfile" "$PSScriptRoot\docker" }
$root = (Resolve-Path "$PSScriptRoot\..").Path
docker run --rm -it -v "${root}:/app" -w /app/formula1 -p 4040:4040 $image @args
