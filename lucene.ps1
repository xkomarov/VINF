#   .\lucene.ps1 python lucene_indexer.py
#   .\lucene.ps1 python search.py "winner:senna AND season:[1985 TO 1990]"
$image = "f1-lucene"
docker image inspect $image *> $null
if ($LASTEXITCODE -ne 0) { docker build -t $image "$PSScriptRoot\docker" }
docker run --rm -it -v "${PSScriptRoot}:/app/formula1" -w /app/formula1 $image @args
