#include "lrun.h"
#include "web_api.h"
#include "lrw_custom_body.h"

vuser_init()
{
    // Initialization code can go here
    return 0;
}

Action()
{
    // Start transaction for adding a new product
    lr_start_transaction("Add_New_Product");

    // Register to find a specific response content to verify the product was added
    web_reg_find("Text=Product added successfully", "Fail=NotFound", LAST);

    // Prepare the data for the new product
    char *productData = "{\"name\":\"New Product\",\"price\":29.99,\"description\":\"A brand new product\",\"category\":\"Electronics\"}";

    // Submit the data to add a new product
    web_submit_data("Add_Product",
        "Action={{{SFCC_SITE_URL}}}/api/products",
        "Method=POST",
        "RecContentType=application/json",
        "Referer={{{SFCC_SITE_URL}}}/api/products",
        "Body={productData}",
        LAST);

    // End transaction for adding a new product
    lr_end_transaction("Add_New_Product", LR_AUTO);

    // Think time after the transaction
    lr_think_time(1);

    return 0;
}

vuser_end()
{
    // Cleanup code can go here
    return 0;
}