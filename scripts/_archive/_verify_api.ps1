# API 服务验证脚本
$baseUrl = "http://localhost:8001"
$all_ok = $true

Write-Host "=== FINAL VERIFICATION: External API Service ===" -ForegroundColor Cyan
Write-Host ""

$passCount = 0
$totalTests = 10

# 1. Health
$testNum = 1
try {
    $r = Invoke-RestMethod -Uri "$baseUrl/api/health" -TimeoutSec 10
    if ($r.status -eq "ok") {
        Write-Host "[PASS $testNum/$totalTests] Health endpoint OK (db=$($r.db_path))" -ForegroundColor Green
        $passCount++
    } else { Write-Host "[FAIL $testNum] Health: status=$($r.status)" -ForegroundColor Red; $all_ok = $false }
} catch { Write-Host "[FAIL $testNum] Health: $($_.Exception.Message)" -ForegroundColor Red; $all_ok = $false }

# 2. Stock list
$testNum = 2
try {
    $r = Invoke-RestMethod -Uri "$baseUrl/api/stocks" -TimeoutSec 10
    if ($r.count -gt 0) {
        Write-Host "[PASS $testNum/$totalTests] Stock list: $($r.count) stocks" -ForegroundColor Green
        $passCount++
    } else { Write-Host "[FAIL $testNum] Stock list: count=$($r.count)" -ForegroundColor Red; $all_ok = $false }
} catch { Write-Host "[FAIL $testNum] Stock list: $($_.Exception.Message)" -ForegroundColor Red; $all_ok = $false }

# 3. Daily data
$testNum = 3
try {
    $r = Invoke-RestMethod -Uri "$baseUrl/api/daily/sh600276?limit=1" -TimeoutSec 10
    if ($r.count -gt 0) {
        Write-Host "[PASS $testNum/$totalTests] Daily 600276: close=$($r.data[0].close)" -ForegroundColor Green
        $passCount++
    } else { Write-Host "[FAIL $testNum] Daily 600276: empty" -ForegroundColor Red; $all_ok = $false }
} catch { Write-Host "[FAIL $testNum] Daily: $($_.Exception.Message)" -ForegroundColor Red; $all_ok = $false }

# 4. Financial statements
$testNum = 4
try {
    $r = Invoke-RestMethod -Uri "$baseUrl/api/financial/sh600276?limit=1" -TimeoutSec 10
    if ($r.count -gt 0) {
        Write-Host "[PASS $testNum/$totalTests] Financial: $($r.count) reports" -ForegroundColor Green
        $passCount++
    } else { Write-Host "[FAIL $testNum] Financial: empty" -ForegroundColor Red; $all_ok = $false }
} catch { Write-Host "[FAIL $testNum] Financial: $($_.Exception.Message)" -ForegroundColor Red; $all_ok = $false }

# 5. Valuation indicators
$testNum = 5
try {
    $r = Invoke-RestMethod -Uri "$baseUrl/api/indicators/valuation/sh600276?limit=1" -TimeoutSec 10
    if ($r.count -gt 0 -and $r.data[0].pe_ttm -lt 50) {
        Write-Host "[PASS $testNum/$totalTests] Valuation: pe_ttm=$($r.data[0].pe_ttm), pe_annual=$($r.data[0].pe_annual)" -ForegroundColor Green
        $passCount++
    } else {
        Write-Host "[FAIL $testNum] Valuation: pe_ttm=$($r.data[0].pe_ttm)" -ForegroundColor Red; $all_ok = $false
    }
} catch { Write-Host "[FAIL $testNum] Valuation: $($_.Exception.Message)" -ForegroundColor Red; $all_ok = $false }

# 6. Technical indicators
$testNum = 6
try {
    $r = Invoke-RestMethod -Uri "$baseUrl/api/indicators/technical/sh600276?limit=1" -TimeoutSec 10
    if ($r.count -gt 0) {
        Write-Host "[PASS $testNum/$totalTests] Technical indicators OK" -ForegroundColor Green
        $passCount++
    } else { Write-Host "[FAIL $testNum] Technical: empty" -ForegroundColor Red; $all_ok = $false }
} catch { Write-Host "[FAIL $testNum] Technical: $($_.Exception.Message)" -ForegroundColor Red; $all_ok = $false }

# 7. Stock summary (aggregated)
$testNum = 7
try {
    $r = Invoke-RestMethod -Uri "$baseUrl/api/summary/sh600276" -TimeoutSec 10
    if ($r.stock_code -eq "sh600276" -and $null -ne $r.price -and $null -ne $r.valuation) {
        Write-Host "[PASS $testNum/$totalTests] Summary: price=$($r.price.close), pe_ttm=$($r.valuation.pe_ttm)" -ForegroundColor Green
        $passCount++
    } else {
        Write-Host "[FAIL $testNum] Summary: stock_code=$($r.stock_code)" -ForegroundColor Red; $all_ok = $false
    }
} catch { Write-Host "[FAIL $testNum] Summary: $($_.Exception.Message)" -ForegroundColor Red; $all_ok = $false }

# 8. PE_TTM bug fix validation (0 < pe_ttm < 50 for 600276)
$testNum = 8
try {
    $r = Invoke-RestMethod -Uri "$baseUrl/api/summary/sh600276" -TimeoutSec 10
    $pe = $r.valuation.pe_ttm
    if ($null -ne $pe -and $pe -gt 0 -and $pe -lt 50) {
        Write-Host "[PASS $testNum/$totalTests] PE_TTM fix: pe_ttm=$pe (range 0-50, was ~69 bug)" -ForegroundColor Green
        $passCount++
    } else {
        Write-Host "[FAIL $testNum] PE_TTM=$pe, expected (0,50)" -ForegroundColor Red; $all_ok = $false
    }
} catch { Write-Host "[FAIL $testNum] PE_TTM: $($_.Exception.Message)" -ForegroundColor Red; $all_ok = $false }

# 9. Capital total_shares valid (66亿, not 3049万 bug)
$testNum = 9
try {
    $r = Invoke-RestMethod -Uri "$baseUrl/api/capital/sh600276" -TimeoutSec 10
    $ts = $r.data[0].total_shares
    $ts_yi = [math]::Round($ts / 1e8, 2)
    if ($r.count -gt 0 -and $ts -gt 1e8) {
        Write-Host "[PASS $testNum/$totalTests] Capital total_shares: $ts_yi 亿 (valid, > 1亿)" -ForegroundColor Green
        $passCount++
    } else {
        Write-Host "[FAIL $testNum] Capital: total_shares=$ts" -ForegroundColor Red; $all_ok = $false
    }
} catch { Write-Host "[FAIL $testNum] Capital: $($_.Exception.Message)" -ForegroundColor Red; $all_ok = $false }

# 10. Metadata tables
$testNum = 10
try {
    $r = Invoke-RestMethod -Uri "$baseUrl/api/metadata/tables" -TimeoutSec 10
    if ($r.count -gt 0) {
        Write-Host "[PASS $testNum/$totalTests] Metadata: $($r.count) table definitions" -ForegroundColor Green
        $passCount++
    } else { Write-Host "[FAIL $testNum] Metadata: count=$($r.count)" -ForegroundColor Red; $all_ok = $false }
} catch { Write-Host "[FAIL $testNum] Metadata: $($_.Exception.Message)" -ForegroundColor Red; $all_ok = $false }

Write-Host ""
if ($all_ok -and $passCount -eq $totalTests) {
    Write-Host "================================================" -ForegroundColor Green
    Write-Host "RESULT: ALL $totalTests API CHECKS PASSED ($passCount/$totalTests)" -ForegroundColor Green
    Write-Host "================================================" -ForegroundColor Green
} else {
    Write-Host "================================================" -ForegroundColor Red
    Write-Host "RESULT: $passCount/$totalTests PASSED" -ForegroundColor Red
    Write-Host "================================================" -ForegroundColor Red
}
Write-Host ""
Write-Host "Service URLs:"
Write-Host "  Base:     http://localhost:8001"
Write-Host "  Swagger:  http://localhost:8001/docs"
Write-Host "  Redoc:    http://localhost:8001/redoc"
Write-Host "  Health:   http://localhost:8001/api/health"
Write-Host ""
Write-Host "Database:"
Write-Host "  File:     E:\AI\Data\A-Shares-data\data\stock_data.duckdb"
