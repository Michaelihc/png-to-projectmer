param(
    [string]$Output = "dist\png-to-projectmer-windows.zip"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$outputPath = if ([IO.Path]::IsPathRooted($Output)) {
    [IO.Path]::GetFullPath($Output)
} else {
    [IO.Path]::GetFullPath((Join-Path $repoRoot $Output))
}
$distRoot = Split-Path -Parent $outputPath
New-Item -ItemType Directory -Path $distRoot -Force | Out-Null

$stageRoot = Join-Path $distRoot (".package-" + [guid]::NewGuid().ToString("N"))
$packageRoot = Join-Path $stageRoot "png-to-projectmer"

$releaseFiles = @(
    "run-webapp.bat",
    "package-release.ps1",
    "requirements.txt",
    "README.md",
    "README.en.md",
    "LICENSE",
    "NOTICE.md",
    "nu22.png",
    "scarletking.png",
    "examples\nu22.layers.json",
    "docs\screenshots\workflow.png",
    "docs\screenshots\triangulation.png",
    "webapp\index.html",
    "webapp\server.py",
    "tools\layered_emblem_to_mer.py",
    "tools\trace_svg.py",
    "tools\png_to_mer_schematic.py",
    "tools\mer_ngon_decomposition.py",
    "tools\mer_triangle_primitives.py",
    "tools\render_preview.py"
)

try {
    New-Item -ItemType Directory -Path $packageRoot -Force | Out-Null
    foreach ($relativePath in $releaseFiles) {
        $source = Join-Path $repoRoot $relativePath
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
            throw "Required release file is missing: $relativePath"
        }
        $destination = Join-Path $packageRoot $relativePath
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        Copy-Item -LiteralPath $source -Destination $destination
    }

    Compress-Archive -LiteralPath $packageRoot -DestinationPath $outputPath -CompressionLevel Optimal -Force
    $archive = Get-Item -LiteralPath $outputPath
    Write-Host "Created $($archive.FullName) ($([math]::Round($archive.Length / 1MB, 2)) MB)"
} finally {
    if (Test-Path -LiteralPath $stageRoot) {
        Remove-Item -LiteralPath $stageRoot -Recurse -Force
    }
}
