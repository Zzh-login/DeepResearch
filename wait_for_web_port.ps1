param(
    [int]$Port = 18923,
    [int]$TimeoutSeconds = 120
)

for ($second = 0; $second -lt $TimeoutSeconds; $second++) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $client.Connect("127.0.0.1", $Port)
        exit 0
    } catch {
        Start-Sleep -Seconds 1
    } finally {
        $client.Close()
    }
}

exit 1
