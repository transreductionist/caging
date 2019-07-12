"""Module for the categorization and caging of a donor."""
import copy
import logging

from sqlalchemy.exc import SQLAlchemyError

from application.exceptions.exception_ultsys_user import UltsysUserNotFoundError
from application.flask_essentials import database
from application.flask_essentials import redis_queue
from application.helpers.build_models import build_model_exists
from application.helpers.build_models import build_model_new
from application.helpers.general_helper_functions import flatten_user_dict
from application.helpers.general_helper_functions import munge_address
from application.helpers.general_helper_functions import validate_user_payload
from application.helpers.model_serialization import from_json
from application.helpers.ultsys_user import find_ultsys_user
from application.models.caged_donor import CagedDonorModel
from application.models.gift import GiftModel
from application.models.queued_donor import QueuedDonorModel
from application.schemas.caged_donor import CagedDonorSchema


def categorize_donor( donor_dict ):
    """The main function that queries the database and matches each user against the donor to get a category.

    donor_dict = {
        "id": None,
        "user_first_name": user_first_name,
        "user_last_name": user_last_name,
        "user_zipcode": user_zipcode,
        "user_address": user_address,
        "user_email_address": user_email_address,
        "user_phone_number": user_phone_number
    }

    A query is made to the database for all users with the donor's last name. Then a loop is made over all the users
    returned and matches made against the fields used for caging:
        category = [ first_name, last_name, zipcode, street_address, email, phone_number ]
    A complete match would look like [ 1, 1, 1, 1, 1, 1, 1 ], and in this case this would indicate the donor exists.

    The first three items in the list [ first_name, last_name, zipcode ] are the base characteristics. The last
    three [ street_address, email, phone_number ] are the discriminators. Given the matches for a particular user
    the category matrix is passed to the function:

        category_weight( category_test_matrix )

    which is a simple, and yet a flexible/configurable function, for determining the category of a donor. For example,
    if the category matrix looks like [ 1, 1, 1, 0, 0, 0 ] the weighting function uses the base fields to determine
    what the discriminators should sum to to assign a category. In this case the sum is 0 and would suggest that, from
    extensive studies on caging across the Ultsys user database, that the donor should be caged. A full explanation
    with supporting data is given on the project Wiki.

    The matrix can be extended to include weighting to each field if needed. Currently, the weighting is strict and
    requires a match on all fields for the user to be categorized as existing. An alternative may be to match either
    on the email address or phone number and street.

    :param donor_dict: The donor dictionary from the front-end.
    :return: A category: new, cage, or exists.
    """

    category_definitions = { 0: 'new', 1: 'cage', 2: 'exists', 3: 'caged' }

    # Check to see if the donor has a user ID.
    if 'id' in donor_dict and donor_dict[ 'id' ]:
        # Function is_user returns category_weight = 2 if found ( exists ), along with user[ 1 ] = [ user_id ]
        # If  category_weight = 1 it might be because it found duplicate users: = [ user_id1, user_id2 ]
        is_user = check_if_user( donor_dict )
        return category_definitions[ is_user[ 0 ] ], is_user[ 1 ]

    # Check to see if the donor has a registered email and if so pull user ID.
    if 'user_email_address' in donor_dict and donor_dict[ 'user_email_address' ]:
        query_parameters = {
            'search_terms': { 'email': { 'eq': donor_dict[ 'user_email_address' ] } },
            'sort_terms': [ ]
        }
        users_with_given_email = find_ultsys_user( query_parameters )
        if users_with_given_email:
            ultsys_user = users_with_given_email[ 0 ]
            return category_definitions[ 2 ], [ ultsys_user[ 'ID' ] ]

    # Check to see if the donor has already been caged.
    if check_if_caged( donor_dict ) == 3:
        return category_definitions[ check_if_caged( donor_dict ) ], []

    # If they don't already exist and are not previously caged: cage the donor.
    query_parameters = {
        'search_terms': { 'lastname': { 'eq': donor_dict[ 'user_last_name' ] } },
        'sort_terms': []
    }
    users_by_last_name = find_ultsys_user( query_parameters )

    # If no last names exist this is a new donor.
    if not users_by_last_name:
        return category_definitions[ 0 ], []

    donor_street = munge_address( donor_dict[ 'user_address' ] )

    user_ids = []
    exists_user_ids = []
    maximum_weight = 0
    for user in users_by_last_name:
        # The identifier in Drupal is uppercase.
        if user[ 'ID' ] not in user_ids:
            # Capture the user so that it isn't considered more than once.
            user_ids.append( user[ 'ID' ] )

            # Initialize the category matrix to no match on any fields: [ 0, 0, 0, 0, 0, 0 ].
            category_match_matrix = [ 0 ] * 6

            # Set a match on last name since query matches here.
            category_match_matrix[ 1 ] = 1

            # Do some basic transformations to the address: set lowercase, remove whitespace and punctuation.
            user_street = munge_address( user[ 'address' ] )

            # Find matches across match matrix.
            if donor_dict[ 'user_first_name' ].lower() == user[ 'firstname' ].lower():
                category_match_matrix[ 0 ] = 1
            if donor_dict[ 'user_zipcode' ] == user[ 'zip' ] and \
                    donor_dict[ 'user_zipcode' ] != 0:
                category_match_matrix[ 2 ] = 1
            if donor_street == user_street and donor_street != '':
                category_match_matrix[ 3 ] = 1
            if donor_dict[ 'user_email_address' ].lower() == user[ 'email' ].lower() and \
                    donor_dict[ 'user_email_address' ] != '':
                category_match_matrix[ 4 ] = 1
            if donor_dict[ 'user_phone_number' ] == user[ 'phone' ] and \
                    donor_dict[ 'user_phone_number' ] != '0':
                category_match_matrix[ 5 ] = 1

            # After matching the user then categorize them as new ( 0 ), cage ( 1 ) or exists ( 2 ).
            weight = category_weight( category_match_matrix )

            # Keep track of the maximum weight found.
            maximum_weight = track_maximum_weight( weight, maximum_weight, exists_user_ids, user[ 'ID' ] )

    return category_definitions[ maximum_weight ], exists_user_ids


def track_maximum_weight( weight, maximum_weight, exists_user_ids, user_id ):
    """Function to track the maximum weight.

    :param weight: The current weight coming from the category_match_matrix.
    :param maximum_weight: The maximum weight found over all iterated users.
    :param exists_user_ids: The user ID's that have been found where donor is the user.
    :param user_id: The current user ID in the iteration.
    :return: Maximum weight
    """
    if weight > maximum_weight:
        maximum_weight = weight

    # Add to ID's and downgrade maximum weight if more than one user exactly matches the donor.
    if weight == 2:
        exists_user_ids.append( user_id )
        if len( exists_user_ids ) > 1:
            maximum_weight = 1

    return maximum_weight


def category_weight( category_test_matrix ):
    """A simple function for determining the category of a donor.

    The category_test_matrix list [ first_name, last_name, zipcode, street_address, email, phone_number ] is passed
    in, and then spliced into:
        base fields: [ first_name, last_name, zipcode ]
        discriminator fields: [ street_address, email, phone_number ]

    :param category_test_matrix: The list [ first_name, last_name, zipcode, street_address, email, phone_number ]
    :return: weight which is an integer 0, 1, or 2
    """

    # Base fields are: [ first, last, zip ]
    # Discriminators: [ street, email, phone ]
    weight = 0

    # Use slice operator to grab parts of the category matrix. Some useful shortcuts:
    #     The notation category_test_matrix[ 3: ] takes from the 3rd element to the end.
    #     category_test_matrix[ :-2 ] on [ 1, 1, 1, 0, 0, 0 ] returns [ 1, 1, 1, 0 ], or dropping last 2 elements.
    base_fields = category_test_matrix[ 0:3 ]
    discriminators = category_test_matrix[ 3: ]
    sum_discriminators = sum( discriminators )
    if base_fields == [ 1, 1, 1 ] and sum_discriminators >= 1:
        if sum_discriminators == 3:
            weight = 2
        else:
            weight = 1
    elif base_fields == [ 1, 1, 1 ] and sum_discriminators == 0:
        weight = 1
    elif base_fields == [ 0, 1, 1 ] and sum_discriminators >= 1:
        weight = 1
    elif base_fields == [ 0, 1, 0 ] and sum_discriminators >= 1:
        weight = 1

    return weight


def check_if_caged( donor_dict ):
    """See if the donor was previously caged.

    :param donor_dict = {
        "id": None,
        "user_first_name": user_first_name,
        "user_last_name": user_last_name,
        "user_zipcode": user_zipcode,
        "user_address": user_address,
        "user_email_address": user_email_address,
        "user_phone_number": user_phone_number
    }
    :return: 3 for caged and 0 for not caged.
    """

    street = munge_address( donor_dict[ 'user_address' ] )

    caged_donor = CagedDonorModel.query \
        .filter_by( user_first_name=donor_dict[ 'user_first_name' ] ) \
        .filter_by( user_last_name=donor_dict[ 'user_last_name' ] ) \
        .filter_by( user_zipcode=donor_dict[ 'user_zipcode' ] )
    for caged_query in caged_donor.all():
        caged_address = munge_address( caged_query.user_address )
        if caged_address.strip() == street and street != '':
            return 3
    return 0


def check_if_user( donor_dict ):
    """See if the donor exists.

    :param donor_dict = {
        "id": None,
        "user_first_name": user_first_name,
        "user_last_name": user_last_name,
        "user_zipcode": user_zipcode,
        "user_address": user_address,
        "user_email_address": user_email_address,
        "user_phone_number": user_phone_number
    }
    :return: 2 for caged and 0 for not caged.
    """

    # A user ID is said to exist and so if one isn't returned there is a problem.
    query_parameters = {
        'search_terms': { 'ID': { 'eq': donor_dict[ 'id' ] } },
        'sort_terms': []
    }
    user_by_id = find_ultsys_user( query_parameters )
    if user_by_id:
        # We are returning a category here: ( category_weight, [ user_id ] )
        return 2, [ user_by_id[ 0 ][ 'ID' ] ]
    raise UltsysUserNotFoundError


@redis_queue.job
def redis_queue_caging( user, transactions, app_config_name ):
    """A function for queueing a caging operation and updating models with caged donor or Ultsys user.

    Here is what the user looks like:

    user: {
      "id": null,
      "user_address": {
        "user_first_name": "Aaron",
        "user_last_name": "Peters",
        "user_zipcode": "22202",
        "user_address": "1400 Crystal City Dr",
        "user_city": "Arlington",
        "user_state": "VA",
        "user_email_address": "apeters@numbersusa.com",
        "user_phone_number": "7038168820"
      },
      "billing_address": {
        "billing_first_name": "Aaron",
        "billing_last_name": "Peters",
        "billing_zipcode": "22202",
        "billing_address": "1400 Crystal City Dr",
        "billing_city": "Arlington",
        "billing_state": "VA",
        "billing_email_address": "apeters@numbersusa.com",
        "billing_phone_number": "7038168820"
      }
      'payment_method_nonce': 'tokencc_bc_string',
      'category': 'queued',
      'customer_id': '476914249',
      'gift_id': 3,
      'searchable_id': UUID( 'd1aeac47-17ce-46ca-9d45-3f540f7a1d85' ),
      'queued_donor_id': 3
    }

    :param user: The user dictionary
    :param transactions: The list of transactions. If this is a Braintree sale, for example, there will be one
           transaction in the list. On the other hand if this is an administrative sale where the method used is
           a check or money order there will be 2 transactions.
    :param app_config_name: The configuration ( PROD, DEV, TEST ) that the app is running.
    :return:
    """

    # This is getting pushed onto the queue outside an application context: create it here.
    from application.app import create_app  # pylint: disable=cyclic-import
    app = create_app( app_config_name )  # pylint: disable=C0103

    with app.app_context():
        # Categorize the user: new, cage, caged, exists.
        # The variable category is a tuple:
        #    category[ 0 ]: the category of the donor.
        #    category[ 1 ]: if category is 'exists' this will hold an ID like [ 1234 ].
        #    If category[ 0 ] is 'cage' it might be donor matched 2 or more users: category[ 1 ] = [ 1234, 5678 ].
        #    If category[ 0 ] is 'exists' then len( category[ 1 ] ) == 1.

        # This is a fix to a mismatch between what the back-end expects and what the front-end is passing.
        # The fix is used at the donate controller to correct the mismatch. It is also used at the reprocess
        # queued donor to create the user dictionary expected for caging. Finally, here we use it because
        # A donor who never got into the queued donor table will be re-queued and can be reprocessed at this point.
        user = validate_user_payload( user )

        donor_dict = copy.deepcopy( user )
        donor_dict = flatten_user_dict( donor_dict )
        category = categorize_donor( donor_dict )
        logging.debug( '***** category: %s', category )

        gross_gift_amount = str( transactions[ 0 ][ 'gross_gift_amount' ] )

        if category[ 0 ] == 'exists':
            ultsys_user_id = category[ 1 ][ 0 ]
            user[ 'id' ] = ultsys_user_id
            build_model_exists( user, gross_gift_amount )
            gift_id = user[ 'gift_id' ]
            gift_model = GiftModel.query.filter_by( id=gift_id ).one_or_none()
            gift_model.user_id = ultsys_user_id
            QueuedDonorModel.query.filter_by( id=user[ 'queued_donor_id' ] ).delete()
        elif category[ 0 ] == 'cage' or category[ 0 ] == 'caged':
            gift_id = user[ 'gift_id' ]
            gift_model = GiftModel.query.filter_by( id=gift_id ).one_or_none()
            gift_model.user_id = -1
            caged_donor_dict = user[ 'user_address' ]
            caged_donor_dict[ 'gift_searchable_id' ] = gift_model.searchable_id
            caged_donor_dict[ 'campaign_id' ] = user[ 'campaign_id' ]
            caged_donor_dict[ 'customer_id' ] = user[ 'customer_id' ]
            caged_donor_model = from_json( CagedDonorSchema(), caged_donor_dict, create=True )
            caged_donor_model.data.gift_id = gift_id
            database.session.add( caged_donor_model.data )
            QueuedDonorModel.query.filter_by( id=user[ 'queued_donor_id' ] ).delete()
        elif category[ 0 ] == 'new':
            ultsys_user_id = build_model_new( user, gross_gift_amount )
            user[ 'id' ] = ultsys_user_id
            gift_id = user[ 'gift_id' ]
            gift_model = GiftModel.query.filter_by( id=gift_id ).one_or_none()
            gift_model.user_id = ultsys_user_id
            QueuedDonorModel.query.filter_by( id=user[ 'queued_donor_id' ] ).delete()

        try:
            database.session.commit()
        except SQLAlchemyError as error:
            database.session.rollback()
            raise error
