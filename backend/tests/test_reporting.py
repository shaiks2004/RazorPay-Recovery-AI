def test_reporting_is_read_only_and_bounded(client):
    assert client.get('/api/v1/reporting/overview').status_code==200
    assert client.get('/api/v1/reporting/recoveries?limit=1000').status_code==422
    assert client.get('/api/v1/reporting/ai').status_code==200
    assert client.get('/api/v1/reporting/executions').status_code==200
    assert client.post('/api/v1/reporting/overview').status_code==405
