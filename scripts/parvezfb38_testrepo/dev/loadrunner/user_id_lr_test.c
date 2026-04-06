#include "lrun.h"
#include "web_api.h"
#include "lrw_custom_body.h"

vuser_init()
{
    // Initialization code can be added here if needed
    return 0;
}

Action()
{
    char* user_id = "12345"; // Example user ID, this can be parameterized

    // Start transaction for DELETE request
    lr_start_transaction("DELETE_User");

    // Register to find a specific response content to validate the request
    web_reg_find("Text=User deleted successfully", "Fail=NotFound", LAST);

    // Make the DELETE request to the specified endpoint
    web_custom_request("Delete_User",
        "URL=https://localhost:8000/{user_id}",
        "Method=DELETE",
        "Resource=0",
        "RecContentType=application/json",
        "Referer=",
        "Body=",
        "Snapshot=t1.inf",
        "Mode=HTTP",
        LAST);

    // End transaction for DELETE request
    lr_end_transaction("DELETE_User", LR_AUTO);

    // Think time after the transaction
    lr_think_time(1);

    return 0;
}

vuser_end()
{
    // Cleanup code can be added here if needed
    return 0;
}