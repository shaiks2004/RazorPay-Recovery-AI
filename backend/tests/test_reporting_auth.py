from app.db.models import Base, RecoveryCase

def test_enforced_principal_scopes_merchant(client):
    client.app.state.settings.reporting_auth_required=True
    with client.app.state.session_factory() as s:
        with s.begin(): s.add_all([RecoveryCase(merchant_id='a',original_payment_id=__import__('uuid').uuid4(),amount=1,currency='INR',source='x',eligibility_result='ELIGIBLE',eligibility_reason_codes=[],status='NEW',correlation_id='a')])
    assert client.get('/api/v1/reporting/overview').status_code==401
    assert client.get('/api/v1/reporting/overview',headers={'x-recover-merchant':'a','x-recover-role':'REPORT_VIEWER'}).json()['total_recovery_cases']==1
