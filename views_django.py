# -*- coding: utf-8 -*-
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseRedirect, Http404, HttpResponseForbidden
from django.shortcuts import render, render_to_response, redirect, get_object_or_404

from driver.models import Phone, User, Profile, Car, Driver
from driver.forms import (ProfileForm, ProfileMetaForm, PhoneForm, PhoneFormset, CarForm,
                          DriverForm, PassportForm, TabletForm)


def render_with_rc(template_name):
    """Renders view with RequestContext"""
    def decorator(view_func):
        @wraps(view_func)
        def new_view_func(request, *args, **kwargs):
            response = view_func(request, *args, **kwargs)
            if isinstance(response, dict):
                return render(request, template_name, response)
            else:
                return response
        return new_view_func
    return decorator

def group_required(*allowed_groups, **options):
    def decorator(view_func):
        @wraps(view_func)
        def new_view_func(request, *args, **kwargs):
            #if not request.user.is_authenticated():
            #    return redirect('login')

            if options.get('admin', True) and request.user.is_superuser:
                return view_func(request, *args, **kwargs)

            for group_name in request.user.groups.values_list('name', flat=True):
                if group_name in allowed_groups:
                    return view_func(request, *args, **kwargs)

            return HttpResponseForbidden(u'Эта часть сайта недоступна для вас')
        return new_view_func
    return decorator


@login_required
@group_required(u'Менеджеры')
@render_with_rc('driver/list.html')
def list_drivers(request):
    return {'profiles': Profile.objects.all()}

@login_required
@group_required(u'Менеджеры')
@render_with_rc('driver/add.html')
def add_driver(request):
    data = request.POST if request.method == 'POST' else None

    phone_form = PhoneForm(data, empty_permitted=True)

    # Передадим в ProfileForm id телефона 
    # что-то типа InlineModelAdmin для Profile и Phone
    if phone_form.is_valid() and phone_form.cleaned_data:
        phone = phone_form.save()
        data = data.copy()
        data['profile-phone'] = phone.id

    profile_form = ProfileForm(data)
    passport_form = PassportForm(data, empty_permitted=True)

    driver_form = DriverForm(data)
    car_form = CarForm(data)
    tablet_form = TabletForm(data, empty_permitted=not request.POST.get('tablet-our_tablet'))

    have_driver_data = driver_form.has_changed() or car_form.has_changed() or tablet_form.has_changed()
    driver_form.empty_permitted=not have_driver_data
    car_form.empty_permitted=not have_driver_data

    if request.method == 'POST':
        if phone_form.is_valid() and profile_form.is_valid() and\
           driver_form.is_valid() and car_form.is_valid() and\
           tablet_form.is_valid() and passport_form.is_valid():

            profile = profile_form.save(commit=False)

            if passport_form.cleaned_data:
                profile.passport = passport_form.save()

            profile.save()

            if have_driver_data:
                driver = driver_form.save(commit=False)
                driver.profile = profile
                driver.car = car_form.save()
                if tablet_form.has_changed():
                    driver.tablet = tablet_form.save()
                driver.save()

            return redirect('list_drivers')

    return {'profile_form': profile_form,
            'phone_form': phone_form,
            'passport_form': passport_form,
            'driver_form': driver_form,
            'car_form': car_form,
            'tablet_form': tablet_form}

@login_required
@group_required(u'Менеджеры')
@render_with_rc('driver/add.html')
def edit_driver(request, profile_id):
    def look_for_driver_data():
        have_driver_data = driver_form.has_changed() or car_form.has_changed() or tablet_form.has_changed()
        driver_form.empty_permitted = not have_driver_data
        car_form.empty_permitted = not have_driver_data
        return have_driver_data

    def delete_tablet():
        tablet.delete()
        driver.tablet = None
        driver.save()

    def sync_phone_with_user():
        phone.confirmed = False
        phone.save()

        phone.user.username = phone.number
        phone.user.set_unusable_password()
        phone.user.save()

    data = request.POST if request.method == 'POST' else None

    profile_id = int(profile_id) # TODO raise 404 if not int
    profile = get_object_or_404(Profile, pk=profile_id)
    phone = profile.phone

    driver = Driver.objects.get_or_none(profile=profile)
    tablet = driver.tablet if driver else None

    phone_form = PhoneForm(data, instance=phone, empty_permitted=True)

    if phone_form.is_valid() and phone_form.has_changed():
        phone = phone_form.save()
        if phone.user and phone.number != phone.user.username:
            sync_phone_with_user()

    # передадим в profile_form id телефона для поля Profile.phone
    if phone and data:
        data = data.copy()
        data['profile-phone'] = phone.id

    profile_form = ProfileForm(data, instance=profile)
    passport_form = PassportForm(data, instance=profile.passport, empty_permitted=True)

    driver_form = DriverForm(data, instance=driver)
    car_form = CarForm(data, instance=driver.car if driver else None)
    tablet_form = TabletForm(data, instance=tablet, empty_permitted=True, initial={'our_tablet': bool(tablet)})

    have_driver_data = look_for_driver_data()

    if request.method == 'POST':
        if not tablet_form['our_tablet'].value() and tablet:
            delete_tablet()

            tablet_form = TabletForm(data, empty_permitted=True)
            have_driver_data = look_for_driver_data()

        if phone_form.is_valid() and profile_form.is_valid() and\
           driver_form.is_valid() and car_form.is_valid() and\
           tablet_form.is_valid() and passport_form.is_valid():

            profile = profile_form.save()

            if passport_form.has_changed():
                profile.passport = passport_form.save()
                profile.save()

            if have_driver_data:
                driver = driver_form.save(commit=False)
                driver.profile = profile
                driver.car = car_form.save()

                if tablet_form.has_changed():
                    driver.tablet = tablet_form.save()

                driver.save()

            return redirect('list_drivers')

    return {'profile_form': profile_form,
            'phone_form': phone_form,
            'passport_form': passport_form,
            'driver_form': driver_form,
            'car_form': car_form,
            'tablet_form': tablet_form}
