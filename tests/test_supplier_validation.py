from app.schemas.catalog import SupplierCreate
import pytest

def test_supplier_ruc_requires_eleven_digits():
    with pytest.raises(ValueError):
        SupplierCreate(code='PRV-1', name='Proveedor', tax_id='123')

def test_supplier_ruc_is_normalized_and_address_is_kept():
    supplier = SupplierCreate(code='PRV-20123456789', name='Proveedor', tax_id='20123456789', address='Av. Principal 123')
    assert supplier.tax_id == '20123456789'
    assert supplier.address == 'Av. Principal 123'
