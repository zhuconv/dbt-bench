Create a model called "obt_product_inventory" that adds product details to every item in our inventory.

Keep one row for every fact_inventory record with all of its columns, and add every dim_products column except attachments, leaving those columns empty when the product is not in dim_products. Take product_id from dim_products, and keep the inventory record's own product_id in a column named ipd.
