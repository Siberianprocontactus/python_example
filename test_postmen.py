import pytest
from django.core.urlresolvers import reverse
from mock import patch, MagicMock
from model_mommy import mommy

from . import get_testdata


def make_carrier_service():
    tnt = mommy.make('Carrier', name='TNT', config={})
    tnt_express = mommy.make('CarrierService', carrier=tnt, name='TNT Express')
    mommy.make('CarrierMapping', carrier=tnt, provider='postmen', step='process', link_name='')

    return tnt, tnt_express

def make_shipment(status='pending', **kwargs):
    carrier, service = make_carrier_service()

    frm = mommy.make('Address', country='HK')
    to = mommy.make('Address', country='CA')

    shipment = mommy.make('Shipment',
                          status=status,
                          carrier_service=service.name,
                          source=frm,
                          destination=to,
                          **kwargs)

    return shipment


@pytest.mark.django_db
@patch('shipment.views.request_label')
def test_api_shipment_process_async(celery_task, client):
    """
    API call creates a shipment,
    changes its status to in_progress
    and calls celery task
    """

    from shipment.models import Shipment

    user = mommy.make('User', username='test_user')
    client.force_authenticate(user=user)

    carrier, service = make_carrier_service()

    data = {
        "async": True,
        "reference": "REF1",
        "source": {
            "country": "HKG",
        },
        "destination": {
            "country": "CAN",
        },
        "packages": [{
            "width": 2,
            "height": 2,
            "length": 2,
            "weight": "2.000",
            "items": [{
                "item_type": "a thing",
                "sku": "fk34kf",
                "description": "thingy thing",
                "manufacturer": "AU",
                "length": 2,
                "width": "2.00",
                "height": 2,
                "weight": 2.0,
                "customs_value": {
                    "amount": 0.0,
                    "currency": "USD"
                }
            }],
        }],
        "carrier_service": service.name,
        "tax_state": "shipper",
    }

    assert Shipment.objects.count() == 0

    resp = client.post(reverse('api:shipment-process'),
                       data,
                       format='json')
    print(resp.status_code)
    print(resp)

    assert Shipment.objects.count() == 1
    shipment = Shipment.objects.first()
    assert shipment.status == 'in_progress'

    celery_task.delay.assert_called_once_with(shipment.id)


@pytest.mark.django_db
@patch('carrier.providers.postmen.service.Postmen')
def test_ask_postmen_to_create_label(postmen):
    """
    This task calls Postmen API with async=true flag,
    stores id field of the Postmen response
    and keeps shipment status 'in_progress'
    """
    from carrier.providers.postmen.models import PostmenLabelRequest
    from shipment.models import Shipment
    from shipment.tasks import request_label

    api = postmen.return_value
    api.create.return_value = {"id": "1-2-3-4-5"}
    #api.create.side_effect = Exception('OMG!')

    shipment = make_shipment(status='in_progress')

    request_label(shipment.id)

    assert api.create.call_count == 1
    args, kwargs = api.create.call_args
    assert args[1]['async'] == True

    label_request = PostmenLabelRequest.objects.get(shipment=shipment)
    assert label_request.label_id == '1-2-3-4-5'

    assert Shipment.objects.get(pk=shipment.pk).status == 'in_progress'


@pytest.mark.django_db
@patch('shipment.views.download_and_store_label')
def test_postmen_callback(celery_task, client):
    """This view does nothing - it just responds and calls a celery task"""

    data = get_testdata('postmen-create-label-hook-resp.json')
    shipment = make_shipment()
    mommy.make('PostmenLabelRequest', label_id=data['data']['id'], shipment=shipment)

    resp = client.post(reverse('postmen-callback'),
                       data,
                       format='json')
    print(resp)

    assert resp.status_code == 200
    assert resp.data == {}
    celery_task.delay.assert_called_once_with(shipment.id)


@pytest.mark.django_db
@patch('carrier.providers.postmen.service.requests')
@patch('carrier.providers.postmen.service.Postmen')
def test_download_and_store_postmen_label(postmen, requests, settings, tmpdir):
    """
    This task gets a label info from postmen, and then downloads and stores label
    """
    from shipment.models import Shipment
    from shipment.tasks import download_and_store_label

    settings.MEDIA_ROOT = str(tmpdir)
    postmen_resp = get_testdata('postmen-create-label-hook-resp.json')['data']

    api = postmen.return_value
    api.get.return_value = postmen_resp

    label_download_resp = MagicMock()
    label_download_resp.status_code = 200
    label_download_resp.content = 'SomePDFContent'
    requests.get.return_value = label_download_resp

    label_id = '1-2-3-4-5'
    shipment = make_shipment(status='in_progress')
    mommy.make('PostmenLabelRequest', shipment=shipment, label_id=label_id)

    download_and_store_label(shipment.id)

    api.get.assert_called_once_with('labels', label_id)
    requests.get.assert_called_once_with(postmen_resp['files']['label']['url'])

    assert shipment.documents.count() == 1
    label = shipment.documents.first()
    assert label.document.read() == 'SomePDFContent'

    assert Shipment.objects.get(pk=shipment.pk).status == 'processed'


@pytest.mark.django_db
@patch('shipment.tasks.requests')
def test_call_floship_callback(requests):
    from shipment.serializers import ShipmentSerializer
    from shipment.tasks import call_floship_callback

    user = mommy.make('User', username='test_user')
    settings = mommy.make('UserSettings', user=user, callback_url='http://someurl.com')
    shipment = make_shipment(user=user)

    call_floship_callback(shipment.id)

    requests.post.assert_called_once_with(settings.callback_url,
                                          json=ShipmentSerializer(shipment).data)


@pytest.mark.django_db
@patch('carrier.providers.postmen.service.requests')
@patch('carrier.providers.postmen.service.Postmen')
def test_api_shipment_process(postmen, requests, client):
    from shipment.models import Shipment

    user = mommy.make('User', username='test_user')
    client.force_authenticate(user=user)

    carrier, service = make_carrier_service()

    data = {
        "async": False,
        "reference": "REF1",
        "source": {
            "country": "HK",
        },
        "destination": {
            "country": "CA",
        },
        "packages": [{
            "width": 2,
            "height": 2,
            "length": 2,
            "weight": "2.000",
            "items": [{
                "item_type": "a thing",
                "sku": "fk34kf",
                "description": "thingy thing",
                "manufacturer": "AU",
                "length": 2,
                "width": "2.00",
                "height": 2,
                "weight": 2.0,
                "customs_value": {
                    "amount": 0.0,
                    "currency": "USD"
                }
            }],
        }],
        "carrier_service": service.name,
        "tax_state": "shipper",
    }

    postmen_resp = {"tracking_numbers": ["123"],
                    "files": {
                        "label": {
                            "paper_size": "a4",
                            "url": "https://some/path/to/pdf",
                            "file_type": "pdf"
                        }
                    }}
    api = postmen.return_value
    api.create.return_value = postmen_resp

    label_download_resp = MagicMock()
    label_download_resp.status_code = 200
    label_download_resp.content = 'SomePDFContent'
    requests.get.return_value = label_download_resp

    assert Shipment.objects.count() == 0

    resp = client.post(reverse('api:shipment-process'),
                       data,
                       format='json')
    print(resp.status_code)
    print(resp)

    assert resp.status_code == 201
    assert len(resp.data['documents']) == 1

    assert Shipment.objects.count() == 1
    shipment = Shipment.objects.first()
    assert shipment.status == 'processed'

    assert shipment.documents.count() == 1
    label = shipment.documents.first()
    assert label.document.read() == 'SomePDFContent'

